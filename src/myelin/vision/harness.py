import asyncio
import base64
import json
import os
import time
from uuid import uuid4

from pydantic import Field

from myelin.astra.client import Astra, BudgetExceeded
from myelin.program.bindings import resolve
from myelin.schema import Contract, LocatorSpec, RecordedAction, Trace, ValueRef


class BrowserAction(Contract):
    intent: str
    operation: str
    target: LocatorSpec | None = None
    arguments: dict[str, ValueRef] = Field(default_factory=dict)


class Batch(Contract):
    actions: list[BrowserAction] = Field(min_length=1, max_length=12)


TOOLS = [
    {
        "type": "function",
        "name": "browser_batch",
        "strict": False,
        "description": "Perform ordered browser actions. Each action gets its own observation.",
        "parameters": Batch.model_json_schema(),
    }
]


class Recorder:
    def __init__(self, settings, emit):
        self.settings, self.emit = settings, emit

    async def __call__(
        self,
        workflow,
        inputs,
        environment,
        session=None,
        checkpoint=None,
        remaining_goal=None,
        purpose=None,
        policy_revision=None,
        astra=None,
    ):
        if session is None:
            raise ValueError("orchestrator must supply the owned session")
        store = session.store
        astra = astra or Astra(self.settings, store, self.emit)
        policy_revision = policy_revision or (
            "crm-policy-v1" if environment.app == "crm" else "expense-policy-v1"
        )
        initial = store.observation(await session.snapshot())
        trace = Trace(
            run_id=session.run_id,
            workflow=workflow,
            inputs=inputs,
            environment=environment,
            policy_revision=policy_revision,
            initial_observation=initial.id,
            actions=[],
            usage_response_ids=[],
            steer_marks=[],
            outcome="running",
            final_observation=None,
        )
        instructions = (
            "Complete the user's task autonomously using the visible browser. "
            "Use browser_batch actions: navigate, click, fill, select, check, submit, inspect. "
            "Arguments contain typed ValueRefs {kind:literal,value:...}, {kind:input,key:...}, "
            "{kind:secret,key:...}, or multiply_int with left/right refs. "
            "fill/select/check use argument value; navigate uses url. "
            "Locators use strategy role/label/text/test_id, value, "
            "role (for role strategy), exact. "
            "Use exact accessible labels from the observation. "
            "Credentials are available only via secret refs email,password,tenant. "
            "You may use declared input refs. Never invent credentials. "
            "Copy every requested field exactly, including category; prefer input refs. "
            "Click/submit takes a button/link locator; submit means click that button. "
            "Only the permitted app origin is available. Never access test admin routes. "
            "After each batch inspect the resulting observation. When the goal is complete, "
            "respond with a concise final message. The independent verifier decides success."
        )
        goal = "Create the requested customer invoice and mark it paid."
        if environment.app == "expense":
            goal = "Create exactly one expense matching the inputs and submit it."
            goal += (
                " If amount_cents > 50000, add manager note 'approved by demo'; "
                "otherwise leave it empty."
                if policy_revision == "expense-policy-manager-v2"
                else " Leave the manager note empty."
            )
        prompt = {
            "goal": goal,
            "continuation": remaining_goal,
            "workflow": workflow,
            "inputs": inputs,
            "app_url": self.settings.crm_url,
            "observation": await self._view(session),
        }
        pending = [{"role": "user", "content": json.dumps(prompt)}]
        previous = None
        no_progress = 0
        last_hash = initial.aria_hash
        try:
            async with asyncio.timeout(self.settings.timeout_s):
                while True:
                    kwargs = {
                        "instructions": instructions,
                        "input": pending,
                        "tools": TOOLS,
                        "reasoning": {"effort": "high" if checkpoint else "medium"},
                    }
                    if previous:
                        kwargs["previous_response_id"] = previous
                    response = await astra.respond(
                        purpose or ("repair" if checkpoint else "record"), **kwargs
                    )
                    previous = response.id
                    trace.usage_response_ids = (
                        list(astra.usage)
                        if hasattr(astra, "usage")
                        else trace.usage_response_ids + [response.id]
                    )
                    if getattr(astra, "marks", None):
                        trace.steer_marks = list(astra.marks)
                        trace.policy_revision = astra.marks[-1].policy_revision
                    calls = [item for item in response.output if item.type == "function_call"]
                    if not calls:
                        if response.status != "completed":
                            raise RuntimeError("model response incomplete")
                        trace.outcome = "awaiting_verification"
                        break
                    pending = []
                    for call in calls:
                        if call.name != "browser_batch":
                            raise ValueError("unsupported model tool")
                        batch = Batch.model_validate_json(call.arguments)
                        for action in batch.actions:
                            if len(trace.actions) >= int(os.getenv("MYELIN_MAX_ACTIONS", "80")):
                                raise BudgetExceeded("browser action ceiling reached")
                            action_id = str(uuid4())
                            before = store.observation(await session.snapshot(), action_id)
                            error = None
                            offset = len(session.network)
                            try:
                                args = {
                                    k: resolve(v, inputs, session.variables, session.secrets)
                                    for k, v in action.arguments.items()
                                }
                                await session.perform(
                                    action_id, action.operation, action.target, args
                                )
                            except Exception as exc:
                                error = store.sanitizer.clean(str(exc))[:1200]
                            after = store.observation(await session.snapshot(), action_id)
                            recorded = RecordedAction(
                                id=action_id,
                                response_id=response.id,
                                call_id=call.call_id,
                                intent=action.intent,
                                operation=action.operation,
                                target=action.target,
                                arguments=action.arguments,
                                before_id=before.id,
                                after_id=after.id,
                                request_ids=[e.request_id for e in session.network[offset:]],
                                outcome="failure" if error else "success",
                                error=error,
                            )
                            trace.actions.append(recorded)
                            store.append("trace.jsonl", recorded)
                            await self.emit("action.recorded", recorded.model_dump(mode="json"))
                            trace.final_observation = after.id
                            no_progress = no_progress + 1 if after.aria_hash == last_hash else 0
                            last_hash = after.aria_hash
                            if no_progress >= 8:
                                raise BudgetExceeded("repeated no-progress actions")
                            if error:
                                break
                        view = await self._view(session)
                        pending.append(
                            {
                                "type": "function_call_output",
                                "call_id": call.call_id,
                                "output": json.dumps(view | {"error": error}),
                            }
                        )
                    raw = await session.snapshot()
                    pending.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_image",
                                    "image_url": "data:image/png;base64,"
                                    + base64.b64encode(raw.screenshot).decode(),
                                }
                            ],
                        }
                    )
        except (BudgetExceeded, TimeoutError):
            trace.outcome = "aborted"
        except Exception:
            trace.outcome = "failure"
            raise
        finally:
            store.save("trace.json", trace)
            if getattr(astra, "marks", None):
                trace.steer_marks = list(astra.marks)
                trace.policy_revision = astra.marks[-1].policy_revision
                store.save("trace.json", trace)
            session.trace = trace
            session.model_usage = list(astra.usage.values())
        return trace

    async def _view(self, session):
        raw = await session.snapshot()
        return {
            "url": raw.url,
            "aria": session.store.sanitizer.clean(raw.aria),
            "timestamp": time.time(),
        }
