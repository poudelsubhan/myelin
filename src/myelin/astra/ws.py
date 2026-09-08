"""Single WebSocket event owner; accepted steering is never blindly re-sent."""

import asyncio
import time

from openai import APIConnectionError, AsyncOpenAI
from websockets.exceptions import ConnectionClosed

from myelin.astra.client import Astra
from myelin.schema import SteerMark
from myelin.steer.policy import REVISION, parse


class SteeringUnresolved(RuntimeError):
    pass


class SteeringState:
    def __init__(self):
        self.current = None
        self.terminal = None
        self.accepted_parent = None
        self.committed = False
        self.required = set()
        self.delivered = set()

    def outputs(self, items):
        ids = [i["call_id"] for i in items if i.get("type") == "function_call_output"]
        if len(ids) != len(set(ids)) or set(ids) & self.delivered:
            raise ValueError("duplicate WebSocket tool result")
        if self.required and not self.required <= set(ids):
            raise ValueError("missing required steering tool outputs")
        self.delivered.update(ids)
        self.required.clear()

    def recovery(self, requests):
        if self.accepted_parent and not self.committed:
            raise SteeringUnresolved(
                "Accepted steering has an unresolved continuation; reconcile before reconnecting"
            )
        return {
            "previous_response_id": self.terminal,
            "unaccepted_instructions": [r["text"] for r in requests if not r.get("accepted")],
        }


class WebSocketAstra(Astra):
    def __init__(self, settings, store, emit, session, initial_steer=None):
        super().__init__(settings, store, emit)
        self.session = session
        self.state = SteeringState()
        self.requests = []
        self.marks = []
        self.connection = self.manager = self.client = None
        self.initial_steer = initial_steer
        self.created_count = 0
        self.active_request = None
        self.reconnections = 0

    async def recover_terminal(self, purpose):
        """Retrieve the server checkpoint before reconnecting; never replay tool results."""
        self.state.recovery(self.requests)  # Accepted, unresolved steering must stop.
        if not self.state.current or self.reconnections >= 1 or self.client is None:
            raise SteeringUnresolved("No confirmed terminal checkpoint for socket recovery")
        terminal = await self.client.responses.retrieve(self.state.current)
        if terminal.status != "completed":
            raise SteeringUnresolved("Disconnected response is not a confirmed terminal checkpoint")
        await self.account(terminal, purpose)
        self.state.terminal = terminal.id
        self.store.save("websocket-recovery.json", self.state.recovery(self.requests))
        await self.close()
        self.connection = self.manager = self.client = None
        self.reconnections += 1
        self.active_request = None
        self.state.current = None
        for request in self.requests:
            if not request["accepted"]:
                request["sent"] = False
        await self.connect()
        await self.emit("websocket.reconnected", {"checkpoint_response_id": terminal.id})
        return terminal

    async def queue_steer(self, text):
        predicate = parse(text)
        raw = self.store.observation(await self.session.snapshot(), self.session.action_id)
        request = {
            "text": text,
            "predicate": predicate,
            "observation_id": raw.id,
            "action_id": self.session.action_id,
            "timestamp": time.time(),
            "accepted": False,
            "sent": False,
        }
        self.requests.append(request)
        await self.emit("steer.requested", {k: v for k, v in request.items() if k != "predicate"})
        return {"status": "queued", "observation_id": raw.id}

    async def connect(self):
        self.client = AsyncOpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout_s,
            max_retries=0,
        )
        # Disable opaque SDK reconnect/resend: accepted steering belongs to this connection.
        self.manager = self.client.responses.connect(max_retries=0)
        self.connection = await self.manager.__aenter__()

    async def respond(self, purpose, **kwargs):
        self.check_budget()
        if self.connection is None:
            await self.connect()
        self.state.outputs(kwargs.get("input", []) if isinstance(kwargs.get("input"), list) else [])
        await self.connection.response.create(
            model=self.settings.model, max_output_tokens=8192, **kwargs
        )
        completed = None
        try:
            async with asyncio.timeout(self.settings.timeout_s):
                while True:
                    try:
                        event = await self.connection.recv()
                    except (APIConnectionError, ConnectionClosed, OSError):
                        terminal = await self.recover_terminal(purpose)
                        if terminal.output and any(
                            item.type == "function_call" for item in terminal.output
                        ):
                            # Recorder executes these original calls once, then continues.
                            return terminal
                        if not any(not request["accepted"] for request in self.requests):
                            return terminal
                        # No pending browser outputs: continue at the retrieved terminal,
                        # then resend only unaccepted steering after response.created.
                        resumed = dict(kwargs)
                        resumed["previous_response_id"] = terminal.id
                        resumed["input"] = [{"role": "user", "content": "Continue the task."}]
                        await self.connection.response.create(
                            model=self.settings.model, max_output_tokens=8192, **resumed
                        )
                        continue
                    kind = event.type
                    if kind in (
                        "response.created",
                        "response.completed",
                        "response.incomplete",
                        "response.failed",
                        "response.steer.accepted",
                        "response.steer.pending",
                        "response.steer.failed",
                        "error",
                    ):
                        self.store.append("websocket.jsonl", event.model_dump(mode="json"))
                    if kind == "response.created":
                        self.state.current = event.response.id
                        self.created_count += 1
                        if (
                            self.state.accepted_parent
                            and self.state.current != self.state.accepted_parent
                        ):
                            self.state.committed = True
                            await self.emit("steer.committed", {"response_id": self.state.current})
                        # Send the demo instruction after at least one actual browser tool batch.
                        if self.initial_steer and self.session.action_id:
                            await self.queue_steer(self.initial_steer)
                            self.initial_steer = None
                    if self.state.current and not self.active_request:
                        queued = next((r for r in self.requests if not r["sent"]), None)
                        if queued:
                            queued["sent"] = True
                            self.active_request = queued
                            await self.connection.response.steer(
                                previous_response_id=self.state.current, input=queued["text"]
                            )
                    if kind == "response.steer.accepted":
                        request = self.active_request
                        if request is None:
                            raise SteeringUnresolved("Unexpected steering acceptance")
                        request["accepted"] = True
                        self.state.accepted_parent = event.steer.previous_response_id
                        self.state.committed = False
                        mark = SteerMark(
                            id=event.steer.id,
                            response_id=event.steer.previous_response_id,
                            action_id=request["action_id"],
                            text=request["text"],
                            observation_id=request["observation_id"],
                            timestamp=request["timestamp"],
                            accepted=True,
                            predicate=request["predicate"],
                            policy_revision=REVISION,
                        )
                        self.marks.append(mark)
                        self.store.append("steers.jsonl", mark)
                        await self.emit("steer.accepted", mark.model_dump(mode="json"))
                    elif kind in ("response.completed", "response.incomplete"):
                        completed = event.response
                        await self.account(completed, purpose)
                        self.state.terminal = completed.id
                        if self.active_request and not self.active_request["accepted"]:
                            continue
                        if self.state.accepted_parent == completed.id and not self.state.committed:
                            # The next event is an automatic successor or pending tool requirements.
                            continue
                        if kind == "response.incomplete":
                            reason = getattr(completed.incomplete_details, "reason", None)
                            if reason != "steered":
                                raise SteeringUnresolved(
                                    "Unexpected incomplete response: " + str(reason)
                                )
                            continue
                        self.active_request = None
                        return completed
                    elif kind == "response.steer.pending":
                        if completed is None:
                            raise SteeringUnresolved("Pending steer without terminal response")
                        data = event.model_dump(mode="json")
                        required = data.get("required_input", [])
                        self.state.required = {
                            i["call_id"]
                            for i in required
                            if i.get("type") == "function_call_output"
                        }
                        self.store.save("steer-pending.json", data)
                        return completed
                    elif kind in ("response.steer.failed", "response.failed", "error"):
                        raise SteeringUnresolved("WebSocket event: " + kind)
        except Exception:
            try:
                plan = self.state.recovery(self.requests)
            except SteeringUnresolved as exc:
                plan = {"status": "unresolved", "reason": str(exc)}
            self.store.save("websocket-recovery.json", plan)
            raise

    async def close(self):
        if self.manager:
            await self.manager.__aexit__(None, None, None)
        if self.client:
            await self.client.close()
