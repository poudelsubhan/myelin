import asyncio
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
from myelin.program.executor import Executor
from myelin.schema import Contract, EnvironmentSpec, GateRequest, GateResult, Program, RunResult
from myelin.trace.store import TraceStore
from myelin.verification.admin import Admin
from myelin.verification.oracle import verify
from myelin.vision.harness import Recorder


class RunRequest(Contract):
    workflow: Literal["crm.create_invoice"]
    inputs: dict
    mode: Literal["record", "program", "repair", "full"]
    environment: EnvironmentSpec
    policy_revision: str = "crm-policy-v1"
    program_hash: str | None = None


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
    admin = Admin(settings)
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
            session = Session(settings, store, run_id, tenant)
            await session.open(body.environment, tenant)
            sessions[run_id] = session
            if body.mode == "record":
                trace = await Recorder(settings, emit)(
                    body.workflow, body.inputs, body.environment, session=session
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
                    status="completed" if success else "failed",
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
            else:
                path = settings.runs_dir.parent / "programs" / body.workflow / body.program_hash
                program = Program.model_validate_json((path / "program.json").read_text())
                if program.content_hash() != body.program_hash:
                    raise ValueError("candidate hash mismatch")
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
    async def start_run(body: RunRequest):
        if body.mode in ("repair", "full"):
            raise HTTPException(501, "feature pending its integration gate")
        if body.mode == "program" and (
            not body.program_hash or not re.fullmatch(r"[a-f0-9]{64}", body.program_hash)
        ):
            raise HTTPException(422, "program mode requires a candidate hash")
        if body.environment.app != "crm" or body.policy_revision != "crm-policy-v1":
            raise HTTPException(422, "unsupported environment or policy")
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
        if program.workflow != "crm.create_invoice":
            raise HTTPException(422, "unsupported workflow")
        return {"candidate_hash": candidates.put(program)}

    async def gate_case(program, inputs, environment, run_id):
        store = TraceStore(settings.runs_dir, run_id)

        async def emit(kind, payload):
            return await bus.emit(run_id, kind, store.sanitizer.clean(payload))

        await admin.reset(run_id, environment)
        session = Session(settings, store, run_id, run_id)
        try:
            await session.open(environment, run_id)
            await emit("run.started", {"mode": "program", "gate_case": True})
            result = await Executor(admin, emit)(program, inputs, environment, session)
            await emit(
                "run.finished", {"status": result.status, "result": result.model_dump(mode="json")}
            )
            return result
        finally:
            await session.close()

    async def gate_job(gate_id, request):
        async def emit(kind, payload):
            return await bus.emit(gate_id, kind, payload)

        try:
            result = await GateRunner(settings.runs_dir / "gates", candidates, gate_case, emit)(
                request, gate_id
            )
            gates[gate_id] = result.model_dump(mode="json")
        except Exception as exc:
            gates[gate_id] = {"status": "failed", "error": type(exc).__name__}

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

            baseline = await GateRunner(settings.runs_dir / "gates", candidates, gate_case, emit)(
                baseline_request
            )
        row = await ledger.promote(
            gate,
            body.expected_parent_hash,
            body.mode,
            [c.case_id for c in suite(gate.request.workflow)],
            baseline=baseline,
        )
        await bus.emit(body.gate_id, "ledger.updated", row.model_dump(mode="json"))
        return row

    @app.get("/ledger/{workflow}")
    async def get_ledger(workflow: str):
        if workflow not in ("crm.create_invoice", "expense.submit_expense"):
            raise HTTPException(404, "unknown workflow")
        return {"current": ledger.current(workflow), "rows": ledger.rows(workflow)}

    return app
