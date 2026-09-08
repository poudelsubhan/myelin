"""Native async calls keep their original call IDs until exactly one terminal delivery."""

import asyncio
import json
from dataclasses import dataclass


@dataclass
class PendingCall:
    call_id: str
    task_handle: str
    job: asyncio.Task
    delivered: bool = False


class AsyncTools:
    def __init__(self, store):
        self.store = store
        self.calls = {}
        self.handles = set()

    def launch(self, call_id, task_handle, factory):
        if (
            call_id in self.calls
            or call_id in self.handles
            or task_handle in self.handles
            or not task_handle
        ):
            raise ValueError("call ID and model task handle must be unique across the conversation")
        self.handles.add(task_handle)
        self.calls[call_id] = PendingCall(call_id, task_handle, asyncio.create_task(factory()))
        self.store.append(
            "async-tools.jsonl",
            {"event": "launched", "call_id": call_id, "task_handle": task_handle},
        )

    def completed(self):
        outputs = []
        for call in self.calls.values():
            if call.delivered or not call.job.done():
                continue
            try:
                value = call.job.result()
                if hasattr(value, "model_dump"):
                    value = value.model_dump(mode="json")
                payload = {"task_handle": call.task_handle, "status": "completed", "result": value}
            except (Exception, asyncio.CancelledError) as exc:
                payload = {
                    "task_handle": call.task_handle,
                    "status": "failed",
                    "error": type(exc).__name__,
                }
            output = {
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(payload),
            }
            # Persist before exposing the output; completed IDs remain owned for this conversation.
            self.store.append("async-tools.jsonl", {"event": "delivered", **output})
            call.delivered = True
            outputs.append(output)
        return outputs

    async def wait(self, wait_call_id, handles):
        if wait_call_id in self.calls or wait_call_id in self.handles:
            raise ValueError("wait call identity reused")
        if not set(handles) <= self.handles:
            raise ValueError("unknown task handle")
        self.handles.add(wait_call_id)
        jobs = [c.job for c in self.calls.values() if c.task_handle in handles and not c.job.done()]
        if jobs:
            await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
        outputs = self.completed()
        outputs.append(
            {
                "type": "function_call_output",
                "call_id": wait_call_id,
                "output": json.dumps(
                    {
                        "status": {
                            c.task_handle: "completed" if c.job.done() else "pending"
                            for c in self.calls.values()
                            if c.task_handle in handles
                        }
                    }
                ),
            }
        )
        return outputs

    async def close(self):
        for call in self.calls.values():
            if not call.job.done():
                call.job.cancel()
        await asyncio.gather(*(c.job for c in self.calls.values()), return_exceptions=True)
        return self.completed()


class GateToolProvider:
    def __init__(self, runner, request, store):
        self.runner = runner
        self.request = request.model_copy(deep=True)
        self.registry = AsyncTools(store)

    def tools(self):
        return [
            {
                "type": "function",
                "name": "validate_candidate",
                "async": True,
                "description": "Run the immutable gate. Choose a unique task_handle.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_handle": {"type": "string"},
                        "candidate_hash": {"type": "string"},
                    },
                    "required": ["task_handle", "candidate_hash"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "wait_for_validation",
                "strict": True,
                "description": "Wait for original-call results; this call returns status only.",
                "parameters": {
                    "type": "object",
                    "properties": {"task_handles": {"type": "array", "items": {"type": "string"}}},
                    "required": ["task_handles"],
                    "additionalProperties": False,
                },
            },
        ]

    async def execute(self, call):
        args = json.loads(call.arguments)
        if call.name == "validate_candidate":
            if not getattr(call, "async_", False):
                raise ValueError("native async call flag missing")
            if args["candidate_hash"] != self.request.candidate_hash:
                raise ValueError("cannot mutate the immutable candidate under test")
            self.registry.launch(
                call.call_id, args["task_handle"], lambda: self.runner(self.request)
            )
            return []
        if call.name == "wait_for_validation":
            return await self.registry.wait(call.call_id, args["task_handles"])
        raise ValueError("unknown compiler tool")
