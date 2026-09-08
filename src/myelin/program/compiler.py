"""Model-authored programs are validated before they enter the immutable candidate store."""

import json
from typing import Protocol

from pydantic import ValidationError

from myelin.astra.client import Astra
from myelin.program.bindings import BindingError, allow_url
from myelin.schema import Branch, HttpStep, LiteralRef, MultiplyRef, NamedRef, Program


class ToolProvider(Protocol):
    def tools(self) -> list[dict]: ...
    async def execute(self, call) -> dict: ...


def refs(value):
    if isinstance(value, (NamedRef, LiteralRef)):
        yield value
    elif isinstance(value, MultiplyRef):
        yield from refs(value.left)
        yield from refs(value.right)
    elif hasattr(type(value), "model_fields"):
        for name in type(value).model_fields:
            yield from refs(getattr(value, name))
    elif isinstance(value, dict):
        for child in value.values():
            yield from refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from refs(child)


def validate_program(program, trace, candidates, allowed_origins):
    action_ids = {a.id for a in trace.actions if a.outcome == "success"}
    from myelin.program.dataflow import validate_dataflow

    input_keys = set(trace.inputs)
    if program.workflow != trace.workflow or program.app != trace.environment.app:
        raise BindingError("program workflow/app does not match trace")
    if trace.run_id not in program.compiled_from:
        raise BindingError("missing compilation provenance")
    validate_dataflow(program, input_keys)
    marks = {m.id: m for m in trace.steer_marks if m.accepted}
    has_http_write = False

    def walk(steps):
        nonlocal has_http_write
        for step in steps:
            if not set(step.source_action_ids) <= action_ids:
                raise BindingError("unknown/failed source action ID")
            if isinstance(step, Branch):
                mark = marks.get(step.source_steer_id)
                if not mark or mark.predicate != step.condition:
                    raise BindingError("branch must match an accepted explicit policy steer")
                walk(step.then)
                walk(step.otherwise)
            elif isinstance(step, HttpStep):
                grounded = [
                    c.step
                    for c in candidates
                    if c.confidence == "high"
                    and set(step.source_action_ids) == set(c.source_action_ids)
                ]
                fields = (
                    "method",
                    "url",
                    "path_params",
                    "query",
                    "headers",
                    "body_kind",
                    "body",
                    "extract",
                )
                if not any(
                    all(getattr(step, k) == getattr(c, k) for k in fields) for c in grounded
                ):
                    raise BindingError(
                        f"HTTP step {step.id} differs from supported observed candidate"
                    )
                has_http_write |= step.effect == "write"
            elif step.action == "navigate":
                ref = step.arguments.get("url")
                if isinstance(ref, LiteralRef):
                    allow_url(ref.value, allowed_origins)

    walk(program.steps)
    if not has_http_write:
        raise BindingError("compilation requires an observed mutating HTTP operation")
    return program


class Compiler:
    def __init__(self, settings, store, emit):
        self.settings, self.store, self.emit = settings, store, emit

    async def __call__(
        self, trace, candidates, prior=None, repair_scope=None, steer_marks=None, tool_provider=None
    ):
        astra = Astra(self.settings, self.store, self.emit)
        instructions = (
            "Compile the successful browser trace into a reusable Program JSON object. "
            "When repair_scope is supplied, preserve every prior step outside its list exactly, "
            "including source IDs and conditions. Replace only scoped steps using new repair "
            "HTTP evidence. Keep final_post unchanged; update supported_revisions to the new "
            "trace environment, parent/version and compiled_from provenance. "
            "Return only JSON matching the supplied Program schema. Use provided high-confidence "
            "HTTP candidates exactly for request fields and extracts; do not invent endpoints. "
            "Keep their IDs/dependencies. Retain only necessary successful UI prefix actions "
            "(login and selecting the requested customer). Skip exploratory 404 navigation. "
            "Input refs use field keys. Credentials MUST use secret refs email,password,tenant. "
            "For customer click use target_value argument with an input ref to customer; "
            "the target locator value can retain the trace's example name. "
            "Each operation needs pre/postconditions and actual source_action_ids. "
            "Runtime variable current_url is defined after each executed UI/HTTP step. "
            "HTTP requests share cookies but do not update DOM. Relative response-derived "
            "URLs resolve against the app origin. No UI refresh is needed to pass business "
            "assertions. Every fresh run starts at about:blank with no prior variables. "
            "Predicates use source input/variable/business/response/url/aria. A bare path "
            "is a top-level key; nested paths use $.field[0].field. For whole URL/ARIA use "
            "path empty string. response has status,body,headers,url. Business state has "
            "customers,invoices,payments; final_post should check invoices[0].status == paid. "
            "Postconditions may use extracts from that step. All write steps including login "
            "need unique operation_key. UI submit is a button click. Values are ValueRef objects. "
            "Program policy/revision/workflow must match the supplied trace. Use version 1 "
            "and parent_hash null if no prior, otherwise prior.version+1 and prior hash. "
            "Do not add a final UI action solely to display results; "
            "the console shows business evidence."
        )
        if trace.environment.app == "expense":
            instructions = (
                "Compile the successful expense trace into Program JSON matching the schema. "
                "Use supplied high-confidence expense-login and expense-create HTTP steps exactly. "
                "Use expense-submit HTTP candidate only when supplied. No browser login/prefix is "
                "needed: HTTP login extracts bearer_token. Authorization "
                "takes that raw secret ref; the declared adapter adds Bearer and syncs SPA storage "
                "before a UI step with resume_url. Every UI step following HTTP MUST declare "
                "resume_url variable expense_resume, or /#new for repairing creation. "
                "Keep candidate IDs/dependencies. Use only successful real source action IDs. "
                "All write steps need unique operation_key. UI check/fill uses argument value. "
                "Compare integer cents with integer values. Final assertion checks business "
                "$.expenses[0].status == submitted. Start with no variables and fresh auth. "
                "For accepted steering, produce an explicit Branch whose condition EXACTLY matches "
                "the accepted SteerMark predicate and source_steer_id. In then, fill Manager note "
                "with approved by demo and check Confirm expense above $500 using actual source "
                "actions. Otherwise is empty. Keep a SINGLE common UI Submit expense step after "
                "the branch; it needs resume_url expense_resume for the false path. Preserve "
                "Prior HTTP login/create must be preserved EXACTLY including source IDs for policy "
                "updates; include prior.compiled_from too. Branch pre/post can assert "
                "input amount_cents exists. Use real source IDs and server steer IDs. "
                "Never infer a rule from a screenshot. Set policy_revision from the trace, all "
                "supported revisions to the trace environment, parent_hash to prior_hash, and "
                "version to prior.version+1 or 1 if absent. Include trace.run_id in compiled_from. "
                "When repair_scope exists, keep every prior step outside it EXACTLY unchanged, "
                "including children/source IDs/conditions, and replace only the failed step. "
                "Return complete Program JSON only."
            )
        if (
            repair_scope
            and prior
            and any(s.kind == "ui" and s.id in repair_scope for s in prior.steps)
        ):
            instructions += (
                " The failed step is a genuine UI dependency. Repair it with UI "
                "actions only. You may expand that one step into several UI steps, keeping its "
                "original ID on one of them and assigning new IDs to additional actions. "
                "Preserve the entire prefix/suffix exactly and final_post unchanged. "
                "Use actual new repair action IDs for modal/consent actions. Do not bypass "
                "the UI dependency using HTTP. completed writes must not be repeated."
            )
        payload = {
            "schema": Program.model_json_schema(),
            "trace": trace.model_dump(mode="json"),
            "http_candidates": [c.model_dump(mode="json") for c in candidates],
            "prior": prior.model_dump(mode="json") if prior else None,
            "prior_hash": prior.content_hash() if prior else None,
            "repair_scope": repair_scope,
            "steer_marks": steer_marks or [],
        }
        if tool_provider and hasattr(tool_provider, "compiler_context"):
            payload["tool_context"] = tool_provider.compiler_context()
            instructions += (
                " Follow tool_context.instruction. Actually patch the staged "
                "program before returning JSON."
            )
        pending = [{"role": "user", "content": json.dumps(payload)}]
        previous = None
        corrections = 0
        for attempt in range(20 if tool_provider else 3):
            kwargs = {
                "instructions": instructions,
                "input": pending,
                "reasoning": {"effort": "high"},
                "text": {"format": {"type": "json_object"}},
            }
            if previous:
                kwargs["previous_response_id"] = previous
            if tool_provider:
                kwargs["tools"] = tool_provider.tools()
            response = await astra.respond("compile", **kwargs)
            previous = response.id
            calls = [
                c
                for c in getattr(response, "output", [])
                if c.type in ("function_call", "custom_tool_call", "apply_patch_call")
            ]
            if calls:
                if not tool_provider:
                    raise BindingError("unexpected compiler tool call")
                pending = []
                # Dispatch every native async launch before any synchronous wait.
                ordered = sorted(calls, key=lambda c: not bool(getattr(c, "async_", False)))
                for call in ordered:
                    outputs = await tool_provider.execute(call)
                    pending.extend(outputs if isinstance(outputs, list) else [outputs])
                if not pending:
                    pending = [
                        {
                            "role": "user",
                            "content": "Validation is pending. Analyze a dependency. "
                            "Do not mutate the candidate under test. Then request its results.",
                        }
                    ]
                continue
            registry = getattr(tool_provider, "registry", None)
            if registry and any(not c.delivered for c in registry.calls.values()):
                import asyncio

                await asyncio.gather(
                    *(c.job for c in registry.calls.values()), return_exceptions=True
                )
                pending = registry.completed()
                pending.append(
                    {
                        "role": "user",
                        "content": "Return the complete Program JSON using the actual tool result.",
                    }
                )
                continue
            try:
                program = Program.model_validate_json(response.output_text)
                validate_program(program, trace, candidates, {self.settings.crm_url})
                if repair_scope and prior:
                    from myelin.program.repair_scope import validate_scope

                    validate_scope(program, prior, repair_scope)
                if program.parent_hash != (prior.content_hash() if prior else None):
                    raise BindingError("incorrect parent hash")
                if tool_provider and hasattr(tool_provider, "validate_output"):
                    tool_provider.validate_output(program)
                self.store.save("compiled-program.json", program)
                await self.emit(
                    "program.compiled",
                    {
                        "candidate_hash": program.content_hash(),
                        "program": program.model_dump(mode="json"),
                        "response_id": response.id,
                    },
                )
                return program
            except (ValidationError, BindingError, ValueError) as exc:
                corrections += 1
                if corrections >= 3:
                    raise BindingError("compiler correction limit reached") from exc
                error = str(exc)[:5000]
                self.store.append("compiler-errors.jsonl", {"attempt": attempt, "error": error})
                pending = [
                    {
                        "role": "user",
                        "content": "Correct this validation error and return "
                        "the complete Program JSON: " + error,
                    }
                ]
        raise BindingError("compiler correction limit reached")
