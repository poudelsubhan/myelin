"""A reference owns a fresh browser and tenant with the identical locked policy."""

import time
from decimal import Decimal

from myelin.browser.session import Session
from myelin.schema import RunResult
from myelin.trace.store import TraceStore
from myelin.verification import expense, oracle
from myelin.verification.admin import Admin
from myelin.vision.harness import Recorder


async def run_reference(settings, program, inputs, environment, run_id, bus):
    start = time.monotonic()
    store = TraceStore(settings.runs_dir, run_id)
    admin = Admin(settings)

    async def emit(kind, payload):
        return await bus.emit(run_id, kind, store.sanitizer.clean(payload))

    await admin.reset(run_id, environment)
    before = await admin.state(run_id)
    session = Session(settings, store, run_id, run_id)
    try:
        await session.open(environment, run_id)
        await emit(
            "run.started",
            {
                "mode": "reference",
                "inputs": inputs,
                "environment": environment.model_dump(mode="json"),
                "policy_revision": program.policy_revision,
            },
        )
        trace = await Recorder(settings, emit)(
            program.workflow,
            inputs,
            environment,
            session=session,
            purpose="reference",
            policy_revision=program.policy_revision,
        )
        after = await admin.state(run_id)
        assertions = oracle.verify(
            program.workflow, before, after, inputs, {"revision": program.policy_revision}
        )
        success = trace.outcome == "awaiting_verification" and all(a.passed for a in assertions)
        trace.outcome = "success" if success else "failure"
        store.save("trace.json", trace)
        store.save("business-after.json", after)
        result = RunResult(
            run_id=run_id,
            success=success,
            status="completed" if success else "failed",
            program_hash=None,
            environment=environment,
            final_observation=trace.final_observation or trace.initial_observation,
            oracle_assertions=assertions,
            model_calls=len(session.model_usage),
            model_usd=str(sum(Decimal(u.usd) for u in session.model_usage))
            if all(u.usd is not None for u in session.model_usage)
            and not (store.folder / "api-attempts.jsonl").exists()
            else None,
            wall_ms=int((time.monotonic() - start) * 1000),
            http_requests=session.http_requests,
            ui_actions=session.ui_actions,
            work_units=session.http_requests + 10 * session.ui_actions,
        )
        store.save("result.json", result)
        await emit(
            "run.finished", {"status": result.status, "result": result.model_dump(mode="json")}
        )
        return (
            result,
            (expense if program.app == "expense" else oracle).projection(after, inputs),
            trace.usage_response_ids,
        )
    finally:
        await session.close()
