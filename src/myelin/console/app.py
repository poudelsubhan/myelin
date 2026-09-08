import asyncio
import hmac
import re
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from myelin.browser.session import Session
from myelin.config import Settings
from myelin.console.bus import EventBus
from myelin.contracts import FeatureUnavailable, Services
from myelin.gate.gate import GateRunner, suite
from myelin.gate.ledger import CandidateStore, Ledger
from myelin.gate.runtime import GateRuntime
from myelin.metrics import aggregate
from myelin.orchestration import Pipeline
from myelin.program.executor import Executor
from myelin.repair.loop import RepairLoop
from myelin.schema import (
    Contract,
    EnvironmentSpec,
    GateRequest,
    GateResult,
    Program,
    RunResult,
    UsageRecord,
)
from myelin.trace.store import TraceStore
from myelin.verification.admin import Admin
from myelin.verification.oracle import verify
from myelin.vision.harness import Recorder


class RunRequest(Contract):
    workflow: Literal["crm.create_invoice", "expense.submit_expense"]
    inputs: dict
    mode: Literal["record", "program", "repair", "full"]
    environment: EnvironmentSpec
    policy_revision: str = "crm-policy-v1"
    program_hash: str | None = None
    compile_after: bool = True
    lost_response_step: str | None = None


class PromotionRequest(Contract):
    gate_id: str
    expected_parent_hash: str | None
    mode: Literal["initial", "optimization", "restoration", "policy_change"]


class GateBody(Contract):
    request: GateRequest


class CandidateRequest(Contract):
    program: Program


def create_app(services: Services | None = None):
    settings = Settings.load()
    bus = EventBus(settings.runs_dir)
    statuses, jobs, sessions = {}, set(), {}
    gates = {}
    candidates = CandidateStore(settings.runs_dir.parent / "programs")
    ledger = Ledger(settings.runs_dir / "ledger", candidates)

    @asynccontextmanager
    async def lifespan(app):
        yield
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        for session in sessions.values():
            await session.close()

    app = FastAPI(title="Myelin", lifespan=lifespan)
    app.state.bus = bus

    async def run_job(run_id, body):
        started = time.monotonic()
        store = TraceStore(settings.runs_dir, run_id)

        def emit(kind, payload):
            return bus.emit(run_id, kind, store.sanitizer.clean(payload))

        session = None
        result = None
        status = statuses[run_id]
        app_settings = settings.for_app(body.environment.app)
        admin = Admin(app_settings)
        try:
            await emit(
                "run.started", {"workflow": body.workflow, "mode": body.mode, "inputs": body.inputs}
            )
            if services:
                if body.mode == "record":
                    trace = await services.recorder(body.workflow, body.inputs, body.environment)
                    status.update(status=trace.outcome, trace_id=trace.run_id)
                else:
                    raise FeatureUnavailable("fixture program provider requires candidate")
                return
            tenant = run_id
            await admin.reset(tenant, body.environment)
            before = await admin.state(tenant)
            session = Session(app_settings, store, run_id, tenant)
            await session.open(body.environment, tenant)
            sessions[run_id] = session
            session.lose_response_step = body.lost_response_step
            if body.mode == "record":
                trace = await Recorder(app_settings, emit)(
                    body.workflow,
                    body.inputs,
                    body.environment,
                    session=session,
                    policy_revision=body.policy_revision,
                )
                after = await admin.state(tenant)
                assertions = verify(
                    body.workflow, before, after, body.inputs, {"revision": body.policy_revision}
                )
                success = trace.outcome == "awaiting_verification" and all(
                    a.passed for a in assertions
                )
                trace.outcome = "success" if success else "failure"
                store.save("trace.json", trace)
                usage = session.model_usage
                dollars = (
                    str(sum(Decimal(u.usd) for u in usage))
                    if all(u.usd is not None for u in usage)
                    else None
                )
                result = RunResult(
                    run_id=run_id,
                    success=success,
                    status="completed"
                    if success
                    else ("budget_exhausted" if trace.outcome == "aborted" else "failed"),
                    program_hash=None,
                    environment=body.environment,
                    final_observation=trace.final_observation or trace.initial_observation,
                    oracle_assertions=assertions,
                    model_calls=len(usage),
                    model_usd=dollars,
                    wall_ms=int((time.monotonic() - started) * 1000),
                    http_requests=session.http_requests,
                    ui_actions=session.ui_actions,
                    work_units=session.http_requests + 10 * session.ui_actions,
                )
                status["trace_id"] = run_id
                if success and body.compile_after:
                    program, gate, decision = await Pipeline(
                        app_settings, candidates, ledger, gate_case, emit
                    ).compile_and_promote(trace, store)
                    status.update(candidate_hash=program.content_hash(), gate_id=gate.gate_id)
                    if decision.verdict != "promoted":
                        status["result"] = result.model_dump(mode="json")
                        raise ValueError("compiled candidate was not promoted")

            else:
                path = settings.runs_dir.parent / "programs" / body.workflow / body.program_hash
                program = Program.model_validate_json((path / "program.json").read_text())
                if program.content_hash() != body.program_hash:
                    raise ValueError("candidate hash mismatch")
                if body.mode == "repair":
                    result, repaired, gate = await RepairLoop(
                        app_settings, admin, candidates, ledger, gate_case, emit
                    ).run(program, body.inputs, body.environment, session)
                    if repaired:
                        status.update(candidate_hash=repaired.content_hash(), gate_id=gate.gate_id)
                else:
                    result = await Executor(admin, emit)(
                        program, body.inputs, body.environment, session
                    )
            status.update(
                status="completed" if result.success else "failed",
                result=result.model_dump(mode="json"),
            )
            store.save("result.json", result)
            await emit(
                "run.verified",
                {
                    "success": result.success,
                    "assertions": [a.model_dump() for a in result.oracle_assertions],
                },
            )
        except Exception as exc:
            status.update(status="failed", error=type(exc).__name__)
        finally:
            usage_path = store.folder / "usage.jsonl"
            if usage_path.exists():
                usage_rows = [
                    UsageRecord.model_validate_json(line)
                    for line in usage_path.read_text().splitlines()
                ]
                unique = {r.response_id: r for r in usage_rows}
                status["metrics"] = {
                    "by_purpose": aggregate(usage_rows),
                    "total_model_calls": sum(r.model_calls for r in unique.values()),
                    "total_model_usd": str(sum(Decimal(r.usd) for r in unique.values()))
                    if all(r.usd is not None for r in unique.values())
                    else None,
                    "pipeline_wall_ms": int((time.monotonic() - started) * 1000),
                }
                attempts = store.folder / "api-attempts.jsonl"
                if attempts.exists():
                    status["metrics"]["failed_api_attempts"] = len(
                        attempts.read_text().splitlines()
                    )
                    status["metrics"]["known_response_usd"] = status["metrics"]["total_model_usd"]
                    status["metrics"]["total_model_usd"] = None
                store.save("metrics.json", status["metrics"])
            store.save("status.json", status)
            await emit("run.finished", status)
            if session:
                await session.close()
                sessions.pop(run_id, None)

    @app.get("/")
    async def index():
        return FileResponse(Path(__file__).parent / "static/index.html")

    @app.get("/health")
    async def health():
        return {"status": "ok", "phase": 1}

    @app.post("/runs", status_code=202)
    async def start_run(body: RunRequest, request: Request):
        if body.mode == "full":
            raise HTTPException(501, "feature pending its integration gate")
        if body.mode in ("program", "repair") and (
            not body.program_hash or not re.fullmatch(r"[a-f0-9]{64}", body.program_hash)
        ):
            raise HTTPException(422, "program mode requires a candidate hash")
        permitted = {
            "crm": ("crm.create_invoice", {"crm-policy-v1"}),
            "expense": (
                "expense.submit_expense",
                {"expense-policy-v1", "expense-policy-manager-v2"},
            ),
        }
        if (
            body.environment.app not in permitted
            or body.workflow != permitted[body.environment.app][0]
            or body.policy_revision not in permitted[body.environment.app][1]
        ):
            raise HTTPException(422, "unsupported environment or policy")
        if body.lost_response_step and not hmac.compare_digest(
            request.headers.get("X-Myelin-Demo-Token", ""), settings.demo_token
        ):
            raise HTTPException(403, "internal demo token required for failure injection")
        run_id = str(uuid4())
        statuses[run_id] = {
            "status": "running",
            "trace_id": None,
            "result": None,
            "candidate_hash": body.program_hash,
            "gate_id": None,
            "error": None,
        }
        job = asyncio.create_task(run_job(run_id, body))
        jobs.add(job)
        job.add_done_callback(jobs.discard)
        return {"run_id": run_id}

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str):
        if run_id not in statuses:
            raise HTTPException(404, "unknown run")
        return statuses[run_id]

    @app.get("/runs")
    async def list_runs():
        return [{"run_id": k, **v} for k, v in statuses.items()]

    @app.get("/events")
    async def events(request: Request, run_id: str, after_seq: int = 0):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            raise HTTPException(422, "invalid run ID")
        after_seq = max(after_seq, int(request.headers.get("last-event-id", "0")))
        return StreamingResponse(bus.sse(run_id, after_seq), media_type="text/event-stream")

    @app.get("/artifacts/{run_id}/{artifact:path}")
    async def artifact(run_id: str, artifact: str):
        root = (settings.runs_dir / run_id).resolve()
        path = (root / artifact).resolve()
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]+", run_id)
            or not path.is_relative_to(root)
            or path.suffix not in (".png", ".json", ".jsonl", ".txt")
            or not path.is_file()
        ):
            raise HTTPException(404, "artifact unavailable")
        return FileResponse(path)

    @app.post("/candidates")
    async def candidate(body: CandidateRequest):
        program = body.program
        if program.workflow not in ("crm.create_invoice", "expense.submit_expense"):
            raise HTTPException(422, "unsupported workflow")
        return {"candidate_hash": candidates.put(program)}

    runtime = GateRuntime(settings, bus)
    gate_case, reference_case, outcome = runtime.program, runtime.reference, runtime.outcome

    async def gate_job(gate_id, request):
        async def emit(kind, payload):
            return await bus.emit(gate_id, kind, payload)

        try:
            result = await GateRunner(
                settings.runs_dir / "gates", candidates, gate_case, emit, reference_case, outcome
            )(request, gate_id)
            gates[gate_id] = result.model_dump(mode="json")
        except Exception as exc:
            gates[gate_id] = {"status": "failed", "error": type(exc).__name__}

    @app.post("/native-gates", status_code=202)
    async def native_gate(body: GateBody):
        from myelin.astra.native_validation import native_validation

        if body.request.reference_mode != "full":
            raise HTTPException(422, "native proof requires full reference mode")
        run_id = str(uuid4())
        statuses[run_id] = {"status": "running", "mode": "native_validation"}

        async def native_job():
            try:
                evidence = await native_validation(settings, bus, candidates, body.request, run_id)
                statuses[run_id].update(status="completed", evidence=evidence)
            except Exception as exc:
                statuses[run_id].update(status="failed", error=type(exc).__name__)
                await bus.emit(run_id, "run.finished", statuses[run_id])
            finally:
                TraceStore(settings.runs_dir, run_id).save("status.json", statuses[run_id])

        job = asyncio.create_task(native_job())
        jobs.add(job)
        job.add_done_callback(jobs.discard)
        return {"run_id": run_id}

    @app.post("/gates", status_code=202)
    async def start_gate(body: GateBody):
        gate_id = str(uuid4())
        gates[gate_id] = {"status": "pending"}
        job = asyncio.create_task(gate_job(gate_id, body.request))
        jobs.add(job)
        job.add_done_callback(jobs.discard)
        return {"gate_id": gate_id}

    @app.get("/gates/{gate_id}")
    async def get_gate(gate_id: str):
        if gate_id not in gates:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", gate_id):
                raise HTTPException(404, "unknown gate")
            path = settings.runs_dir / "gates" / gate_id / "result.json"
            if not path.exists():
                raise HTTPException(404, "unknown gate")
            return GateResult.model_validate_json(path.read_text()).model_dump(mode="json")
        return gates[gate_id]

    @app.post("/promotions")
    async def promote(body: PromotionRequest):
        data = await get_gate(body.gate_id)
        if data.get("status") == "pending" or "request" not in data:
            raise HTTPException(409, "gate has no terminal evidence")
        gate = GateResult.model_validate(data)
        baseline = None
        if body.mode == "optimization" and body.expected_parent_hash:
            baseline_request = gate.request.model_copy(
                update={"candidate_hash": body.expected_parent_hash}
            )

            async def emit(kind, payload):
                return await bus.emit(body.gate_id, kind, payload)

            baseline = await GateRunner(
                settings.runs_dir / "gates", candidates, gate_case, emit, reference_case, outcome
            )(baseline_request)
        row = await ledger.promote(
            gate,
            body.expected_parent_hash,
            body.mode,
            [c.case_id for c in suite(gate.request.workflow)],
            baseline=baseline,
        )
        await bus.emit(body.gate_id, "ledger.updated", row.model_dump(mode="json"))
        return row

    @app.post("/demo/reset-ledger")
    async def reset_ledger(request: Request):
        if not settings.demo_token or not hmac.compare_digest(
            request.headers.get("X-Myelin-Demo-Token", ""), settings.demo_token
        ):
            raise HTTPException(403, "internal demo token required")
        workflow = "crm.create_invoice"
        async with ledger.locks[workflow]:
            path = ledger.root / f"{workflow}.current.json"
            if path.exists():
                path.rename(ledger.root / f"{workflow}.reset-{uuid4()}.json")
        return {"status": "reset", "history_preserved": True}

    @app.post("/demo/tenants")
    async def demo_tenant(request: Request):
        if not settings.demo_token or not hmac.compare_digest(
            request.headers.get("X-Myelin-Demo-Token", ""), settings.demo_token
        ):
            raise HTTPException(403, "internal demo token required")
        body = await request.json()
        env = EnvironmentSpec.model_validate(body["environment"])
        return await Admin(settings.for_app(env.app)).reset(body["tenant"], env)

    @app.get("/ledger/{workflow}")
    async def get_ledger(workflow: str):
        if workflow not in ("crm.create_invoice", "expense.submit_expense"):
            raise HTTPException(404, "unknown workflow")
        return {"current": ledger.current(workflow), "rows": ledger.rows(workflow)}

    return app
