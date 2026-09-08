"""One-call, localized locator repair against a real failed observation."""

import json
from uuid import uuid4

from myelin.astra.client import Astra
from myelin.live.learning import validate_live_program
from myelin.live.schema import digest, identifier
from myelin.program.repair_scope import validate_scope
from myelin.runtime.registry import private_json
from myelin.schema import LocatorSpec, UiStep
from myelin.trace.store import TraceStore


async def repair_locator(engine, failed_run_id):
    folder = engine.root / "runs" / identifier(failed_run_id)
    status = json.loads((folder / "status.json").read_text())
    result = json.loads((folder / "result.json").read_text())
    if status["status"] != "failed" or not result.get("failure"):
        raise ValueError("repair requires a definite pre-action failure")
    spec = engine.registry.workflow(status["workflow_id"])
    site = engine.registry.site(spec.site_profile_id)
    effect_scope = digest({"site": site.id, "scope": spec.scope, "profile": status["profile_id"]})
    rows = engine.journal.rows(effect_scope, status["task_key"])
    if any(r["state"] in ("unknown", "dispatched") for r in rows):
        raise ValueError("reconcile uncertain writes before asking for repair")
    program, binding, learned = engine.ledger.get(status["candidate_hash"])
    index = next(i for i, s in enumerate(program.steps) if s.id == result["failed_step_id"])
    failed = program.steps[index]
    if not isinstance(failed, UiStep) or failed.effect != "read" or not failed.target:
        raise ValueError("this repair capability supports pre-write locator failures only")
    checkpoint = result["failure"]["checkpoint"]
    observation = next(
        json.loads(line)
        for line in (folder / "observations.jsonl").read_text().splitlines()
        if json.loads(line)["id"] == checkpoint["observation_id"]
    )
    aria = (folder / observation["aria_path"]).read_text()
    run_id = str(uuid4())
    store = TraceStore(engine.root / "runs", run_id)

    async def emit(kind, payload):
        await engine.bus.emit(run_id, kind, payload)

    astra = Astra(engine.settings, store, emit)
    response = await astra.respond(
        "repair",
        reasoning={"effort": "low"},
        instructions=(
            "Repair only the failed locator using the visible observation. "
            "Return one repair_locator tool call. Do not change the task, "
            "effects, inputs, or outcome. Do not execute actions."
        ),
        input=[
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "goal": spec.goal,
                        "scope": spec.scope,
                        "failed_step": failed.model_dump(mode="json"),
                        "completed_effects": [
                            r["operation_key"] for r in rows if r["state"] == "verified"
                        ],
                        "aria": aria,
                    }
                ),
            }
        ],
        tools=[
            {
                "type": "function",
                "name": "repair_locator",
                "description": "Select the exact visible locator for the failed action",
                "parameters": LocatorSpec.model_json_schema(),
                "strict": False,
            }
        ],
    )
    calls = [x for x in response.output if x.type == "function_call" and x.name == "repair_locator"]
    if len(calls) != 1:
        raise ValueError("repair did not provide one locator")
    target = LocatorSpec.model_validate_json(calls[0].arguments)
    patched = program.model_copy(deep=True)
    patched.steps[index].target = target
    patched.version += 1
    validate_scope(patched, program, [failed.id])
    envelope = binding.model_copy(deep=True)
    envelope.candidate_hash = patched.content_hash()
    # Resolve from the failed run's exact input hash via its persisted task input.
    raw = (
        json.loads((folder / "inputs.json").read_text())
        if (folder / "inputs.json").exists()
        else None
    )
    if raw is not None:
        validate_live_program(patched, envelope, spec, site, raw)
    h = engine.ledger.put(patched, envelope, learned)
    evidence = {
        "status": "candidate",
        "run_id": run_id,
        "candidate_hash": h,
        "parent_hash": program.content_hash(),
        "failed_run_id": failed_run_id,
        "changed_step_id": failed.id,
        "model_calls": len(astra.usage),
        "usage": [u.model_dump(mode="json") for u in astra.usage.values()],
        "scope": "one locator only; requires two new canaries before promotion",
    }
    private_json(store.folder / "repair.json", evidence)
    return evidence
