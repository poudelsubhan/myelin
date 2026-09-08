"""Private local live endpoints, intentionally separate from demo gates."""

import asyncio
import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import Field

from myelin.live.schema import SiteProfile, WorkflowSpec, identifier
from myelin.runtime.registry import private_json
from myelin.schema import Contract


class LiveRunBody(Contract):
    workflow_id: str
    profile_id: str
    mode: Literal["learn", "canary", "execute", "repair"] = "execute"
    inputs: dict
    task_key: str | None = None
    candidate_hash: str | None = None


class ManifestBody(Contract):
    workflow_id: str
    profile_id: str
    rows: list[dict] = Field(min_length=1, max_length=5)


class ValidationBody(Contract):
    workflow_id: str
    profile_id: str
    candidate_hash: str
    inputs: list[dict] = Field(min_length=2, max_length=2)


class CsvBody(Contract):
    workflow_id: str
    profile_id: str
    csv: str = Field(max_length=100000)


class ConnectBody(Contract):
    profile_id: str
    url: str = "https://trello.com/login"
    mode: Literal["dedicated", "existing_chrome"] = "dedicated"


async def local_request(request: Request):
    host = request.headers.get("host", "").split(":")[0]
    if host not in ("localhost", "127.0.0.1", "[", "testserver"):
        raise HTTPException(403, "live console is local only")
    origin = request.headers.get("origin")
    if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
        raise HTTPException(403, "cross-origin live access prohibited")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "cross-site live access prohibited")


def live_router(engine, jobs):
    router = APIRouter(prefix="/live", dependencies=[Depends(local_request)])
    active_ids = set()

    def enqueue(kind, function):
        job_id = str(uuid4())
        active_ids.add(job_id)
        path = engine.root / "jobs" / (job_id + ".json")
        private_json(path, {"status": "pending", "kind": kind})

        async def job():
            try:
                private_json(path, {"status": "running", "kind": kind})
                result = await function(job_id)
                private_json(path, {"status": "completed", "kind": kind, "result": result})
            except Exception as exc:
                private_json(path, {"status": "failed", "kind": kind, "error": str(exc)[:1000]})
            finally:
                active_ids.discard(job_id)

        task = asyncio.create_task(job())
        jobs.add(task)
        task.add_done_callback(jobs.discard)
        return {"job_id": job_id, "run_id": job_id}

    @router.get("")
    async def ui():
        return FileResponse(Path(__file__).parents[1] / "console/static/live.html")

    @router.get("/registry")
    async def registry():
        return engine.registry.listing()

    @router.get("/demo-inputs")
    async def demo_inputs():
        path = engine.root / "manifest.json"
        return json.loads(path.read_text()) if path.exists() else {}

    @router.get("/history")
    async def history():
        paths = sorted(
            (engine.root / "runs").glob("*/status.json"), key=lambda p: p.stat().st_mtime
        )
        return {"runs": [json.loads(p.read_text()) for p in paths[-40:]]}

    @router.get("/programs/{candidate_hash}")
    async def program(candidate_hash: str):
        program, binding, _ = engine.ledger.get(candidate_hash)
        return {
            "program": program.model_dump(mode="json"),
            "binding": binding.model_dump(mode="json"),
        }

    @router.get("/replays/{run_id}")
    async def replay(run_id: str):
        folder = engine.root / "runs" / identifier(run_id)
        trace = json.loads((folder / "trace.json").read_text())
        frames = [
            {
                "intent": a["intent"],
                "operation": a["operation"],
                "url": f"/live/frames/{run_id}/{a['after_id']}",
            }
            for a in trace["actions"]
            if (folder / "observations" / (a["after_id"] + ".png")).exists()
        ]
        return {
            "label": "Recorded Astra learning — saved evidence",
            "run_id": run_id,
            "frames": frames,
        }

    @router.get("/frames/{run_id}/{observation_id}")
    async def frame(run_id: str, observation_id: str):
        return FileResponse(
            engine.root
            / "runs"
            / identifier(run_id)
            / "observations"
            / (identifier(observation_id) + ".png")
        )

    @router.get("/implementation/{component}")
    async def implementation(component: str):
        sources = {
            "effects": "live/effects.py",
            "http": "live/http_recipes.py",
            "repair": "live/repair.py",
            "astra": "astra/client.py",
            "steering": "astra/ws.py",
            "async": "astra/native_validation.py",
        }
        if component not in sources:
            raise HTTPException(404, "unknown component")
        return {
            "path": "src/myelin/" + sources[component],
            "code": (Path(__file__).parents[1] / sources[component]).read_text(),
        }

    @router.post("/repairs/{failed_run_id}", status_code=202)
    async def repair(failed_run_id: str):
        from myelin.live.repair import repair_locator

        identifier(failed_run_id)
        return enqueue("repair", lambda _: repair_locator(engine, failed_run_id))

    @router.post("/batches/csv", status_code=202)
    async def csv_batch(body: CsvBody):
        from myelin.live.batch import parse_csv

        try:
            spec = engine.registry.workflow(body.workflow_id)
            rows = parse_csv(body.csv, spec.input_schema["properties"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return enqueue(
            "batch",
            lambda job_id: engine.batch(body.workflow_id, body.profile_id, rows, batch_id=job_id),
        )

    @router.post("/sites")
    async def site(body: SiteProfile):
        try:
            return {
                "id": body.id,
                "site_profile_hash": engine.registry.put(body),
                "profile_status": "connected_unverified",
            }
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/workflows")
    async def workflow(body: WorkflowSpec):
        try:
            return {"id": body.id, "spec_hash": engine.registry.put(body)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/workflows/propose")
    async def propose():
        raise HTTPException(
            501, "goal-to-spec discovery is an extension; register a reviewed contract"
        )

    async def attach(profile_id):
        await engine.profiles.existing_browser(profile_id)
        return {"status": "connected_unverified", "profile_id": profile_id}

    @router.post("/profiles/connect")
    async def connect(body: ConnectBody):
        try:
            if body.mode == "existing_chrome":
                engine.profiles.use_everyday_chrome(body.profile_id)
                return enqueue("connect", lambda _: attach(body.profile_id))
            return await engine.profiles.connect(body.profile_id, body.url)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/profiles/{profile_id}/finish")
    async def finish(profile_id: str):
        return await engine.profiles.finish(identifier(profile_id))

    @router.post("/runs", status_code=202)
    async def run(body: LiveRunBody):
        try:
            spec, _, inputs = engine.context(body.workflow_id, body.profile_id, body.inputs)
            if body.task_key and body.task_key != inputs[spec.task_key_field]:
                raise ValueError("task_key must match the structured input")
            if body.mode in ("canary", "repair"):
                raise ValueError("use bounded /live/validate; live model repair is an extension")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return enqueue(
            "run",
            lambda job_id: engine.run(
                body.workflow_id,
                body.profile_id,
                body.inputs,
                body.mode,
                body.candidate_hash,
                run_id=job_id,
            ),
        )

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str):
        path = engine.root / "runs" / identifier(run_id) / "status.json"
        if not path.exists():
            raise HTTPException(404, "unknown live run")
        return json.loads(path.read_text())

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str):
        path = engine.root / "jobs" / (identifier(job_id) + ".json")
        if not path.exists():
            raise HTTPException(404, "unknown live job")
        data = json.loads(path.read_text())
        if data["status"] in ("running", "pending") and job_id not in active_ids:
            data.update(
                status="interrupted", error="Server restarted; resume the original task keys"
            )
        return data

    @router.post("/validate", status_code=202)
    async def validate(body: ValidationBody):
        return enqueue(
            "validate",
            lambda _: engine.validate(
                body.workflow_id, body.profile_id, body.candidate_hash, body.inputs
            ),
        )

    @router.post("/batches", status_code=202)
    async def batch(body: ManifestBody):
        return enqueue(
            "batch",
            lambda job_id: engine.batch(
                body.workflow_id, body.profile_id, body.rows, batch_id=job_id
            ),
        )

    @router.get("/events")
    async def events(request: Request, run_id: str, after_seq: int = 0):
        identifier(run_id)
        after_seq = max(after_seq, int(request.headers.get("last-event-id", "0")))
        return StreamingResponse(engine.bus.sse(run_id, after_seq), media_type="text/event-stream")

    @router.post("/profiles/{profile_id}/probe")
    async def probe(profile_id: str, request: Request):
        from myelin.live.schema import https_origin
        from myelin.trace.sanitize import Sanitizer

        body = await request.json()
        url = body["url"]
        https_origin(url)
        browser = await engine.profiles.existing_browser(identifier(profile_id))
        page = await browser.contexts[0].new_page()
        sanitizer = Sanitizer()
        traffic = []

        def record(req):
            if req.method not in ("GET", "HEAD", "OPTIONS") and req.url.startswith("https://"):
                try:
                    payload = req.post_data_json
                except Exception:
                    payload = "non-json body omitted"
                traffic.append(
                    sanitizer.clean({"method": req.method, "url": req.url, "body": payload})
                )

        page.on("request", record)
        await page.goto(url, wait_until="domcontentloaded")
        if not hasattr(engine, "probes"):
            engine.probes = {}
        engine.probes[profile_id] = (page, traffic)
        return {"status": "setup_probe", "url": page.url}

    @router.get("/profiles/{profile_id}/probe")
    async def probe_read(profile_id: str):
        page, traffic = engine.probes[identifier(profile_id)]
        data = {
            "url": page.url,
            "aria": await page.locator("body").aria_snapshot(),
            "test_ids": await page.locator("[data-testid]").evaluate_all(
                "els => els.map(e => ({id:e.getAttribute('data-testid'),tag:e.tagName,"
                "text:e.textContent.slice(0,150),label:e.getAttribute('aria-label')}))"
            ),
            "traffic": traffic,
        }
        private_json(engine.root / "setup" / "probe.json", data)
        return data

    @router.get("/capabilities")
    async def capabilities():
        return {
            "implemented": [
                "ui",
                "persistent_auth",
                "existing_chrome",
                "effect_journal",
                "readback",
                "serial_json_batch",
                "two_canary_gate",
            ],
            "extensions": [
                "http_optimization",
                "live_repair",
                "csv",
                "goal_to_spec",
                "frames",
                "uploads",
                "popup_switching",
            ],
            "verified_external_sites": [],
        }

    return router
