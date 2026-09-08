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
from myelin.program.executor import Executor
from myelin.schema import Contract, EnvironmentSpec, Program, RunResult
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


class CandidateRequest(Contract):
    program: Program


def create_app(services: Services | None = None):
    settings = Settings.load()
    bus = EventBus(settings.runs_dir)
    admin = Admin(settings)
    statuses, jobs, sessions = {}, set(), {}

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
        candidate_hash = program.content_hash()
        folder = settings.runs_dir.parent / "programs" / program.workflow / candidate_hash
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "program.json"
        if not path.exists():
            with path.open("x") as f:
                f.write(program.canonical_json())
        return {"candidate_hash": candidate_hash}

    return app
