"""Serial live orchestration. No reset/reference scheduler is reachable here."""

import json
import time
from dataclasses import replace
from uuid import uuid4

from myelin.browser.session import Session
from myelin.console.bus import EventBus
from myelin.live.derived import derive
from myelin.live.effects import EffectBoundary
from myelin.live.journal import Journal, UnresolvedEffect
from myelin.live.learning import compile_ui, validate_live_program
from myelin.live.ledger import LiveLedger
from myelin.live.profiles import ProfileManager
from myelin.live.runtime import LiveRuntime
from myelin.live.schema import digest
from myelin.live.verification import Verifier
from myelin.program.executor import Executor
from myelin.runtime.registry import Registry, private_directory, private_json
from myelin.schema import EnvironmentSpec, Trace
from myelin.trace.store import TraceStore
from myelin.vision.harness import Recorder


class LiveOrchestrator:
    def __init__(
        self, settings, root, *, session_factory=Session, recorder_factory=Recorder, verifier=None
    ):
        self.settings, self.root = settings, private_directory(root)
        self.registry = Registry(self.root)
        self.journal = Journal(self.root / "live.sqlite")
        self.ledger = LiveLedger(self.root / "ledger", self.journal)
        self.profiles = ProfileManager(self.root / "auth")
        self.bus = EventBus(self.root / "runs")
        self.session_factory, self.recorder_factory = session_factory, recorder_factory
        self.verifier = verifier or Verifier()

    def context(self, workflow_id, profile_id, raw):
        spec = self.registry.workflow(workflow_id)
        site = self.registry.site(spec.site_profile_id)
        if profile_id != site.auth_profile_id:
            raise ValueError("profile does not match the frozen workflow")
        inputs = derive(spec, raw)
        return spec, site, inputs

    async def resume_recording(self, session, effects, spec, inputs):
        verified = {
            row["operation_key"]
            for row in self.journal.rows(effects.scope, effects.task)
            if row["state"] == "verified"
        }
        if not verified:
            return [], {}, []
        for key in verified:
            result = await self.verifier.verify(
                session, spec, inputs, effects.effects[key].assertions
            )
            if result.status != "verified":
                raise UnresolvedEffect("previously completed effect no longer verifies")
        matches = []
        for path in (self.root / "runs").glob("*/trace.json"):
            mapping_path = path.parent / "effect-bindings.json"
            if not mapping_path.exists():
                continue
            prior = Trace.model_validate_json(path.read_text())
            if prior.inputs != inputs:
                continue
            mapping = json.loads(mapping_path.read_text())
            indexes = [
                i
                for i, a in enumerate(prior.actions)
                if mapping.get(a.id) in verified and a.outcome == "success"
            ]
            if indexes:
                end = max(indexes) + 1
                # Retain successful read-only preparation too: rich editors may
                # restore an unsaved draft on the next browser connection.
                while end < len(prior.actions):
                    action = prior.actions[end]
                    if action.outcome != "success" or mapping.get(action.id):
                        break
                    end += 1
                prefix = prior.actions[:end]
                prefix_map = {a.id: mapping[a.id] for a in prefix if a.id in mapping}
                if set(prefix_map.values()) == verified:
                    matches.append((path.stat().st_mtime, prefix, prefix_map, prior.run_id))
        if not matches:
            raise UnresolvedEffect("completed effects lack a recoverable recording prefix")
        _, prefix, mapping, run_id = max(matches, key=lambda row: row[0])
        return prefix, mapping, [run_id]

    async def run(
        self,
        workflow_id,
        profile_id,
        raw,
        mode="execute",
        candidate_hash=None,
        *,
        run_id=None,
        _canary=False,
    ):
        if mode not in ("learn", "execute", "canary") or (mode == "canary" and not _canary):
            raise ValueError("canaries can only be dispatched by bounded validation")
        spec, site, inputs = self.context(workflow_id, profile_id, raw)
        scope = self.ledger.scope(spec, site)
        # Journal scope deliberately excludes spec revision: a changed contract cannot
        # manufacture a new create identity for the same business task.
        effect_scope = digest({"site": site.id, "scope": spec.scope, "profile": profile_id})
        task = inputs[spec.task_key_field]
        run_id = run_id or str(uuid4())
        folder = private_directory(self.root / "runs" / run_id)
        store = TraceStore(self.root / "runs", run_id)
        private_json(folder / "inputs.json", inputs)
        started, session = time.monotonic(), None
        status = {
            "run_id": run_id,
            "workflow_id": workflow_id,
            "profile_id": profile_id,
            "task_key": task,
            "scope_hash": scope,
            "spec_hash": digest(spec),
            "candidate_hash": candidate_hash,
            "mode": mode,
            "status": "running",
            "model_calls": 0,
            "usage": [],
            "timing_ms": {},
            "reused": False,
        }

        async def emit(kind, payload):
            return await self.bus.emit(run_id, kind, store.sanitizer.clean(payload))

        private_json(folder / "status.json", status)
        await emit("live.task.started", {"task_key": task, "mode": mode, "scope": spec.scope})
        try:
            with self.journal.lease("profile:" + profile_id):
                previous = self.journal.preflight(effect_scope, task, inputs)
                if previous:
                    status.update(status="verified", evidence=previous, reused=True)
                    return status
                if mode != "learn":
                    current = self.ledger.current(scope)
                    if mode == "execute":
                        if candidate_hash and candidate_hash != current:
                            raise ValueError("execute requires the current validated candidate")
                        candidate_hash = current
                    if not candidate_hash:
                        raise ValueError("no current validated live program")
                    program, binding, learned_task = self.ledger.get(candidate_hash)
                    if mode == "canary" and task == learned_task:
                        raise ValueError("learning input cannot be replayed as a canary")
                    validate_live_program(program, binding, spec, site, inputs)
                    status["candidate_hash"] = candidate_hash
                settings = replace(
                    self.settings,
                    crm_url=site.start_url,
                    runs_dir=self.root / "runs",
                    timeout_s=max(self.settings.timeout_s, 600),
                )
                session = self.session_factory(
                    settings, store, run_id, task, site=site, profiles=self.profiles
                )
                env = EnvironmentSpec(
                    app=site.id, revision=digest(site), seed_version="not-applicable"
                )
                effects = EffectBoundary(
                    self.journal, spec, inputs, effect_scope, self.verifier, emit
                )
                session.effects = effects
                await session.open(env, task)
                try:
                    await self.verifier.identity(session, site, spec, inputs)
                except Exception as exc:
                    status["status"] = "needs_user_authentication"
                    await emit("profile.needs_auth", {"profile_id": profile_id})
                    raise ValueError("sign in or correct the expected account/board") from exc
                await emit("profile.ready", {"profile_id": profile_id, "scope": spec.scope})
                await effects.reconcile(session)
                phase = time.monotonic()
                if mode == "learn":
                    prefix, prior_mapping, prior_runs = await self.resume_recording(
                        session, effects, spec, inputs
                    )
                    trace = await self.recorder_factory(settings, emit)(
                        spec.id,
                        inputs,
                        env,
                        session=session,
                        live_spec=spec,
                        policy_revision=digest(spec),
                        remaining_goal=(
                            "Continue only missing effects on the existing card. "
                            "Use the supplied card_url runtime variable to open it; "
                            "never repeat create_card. The compiler retains the verified prefix."
                            if prefix
                            else None
                        ),
                    )
                    status["timing_ms"]["record"] = int((time.monotonic() - phase) * 1000)
                    phase = time.monotonic()
                    evidence = await self.verifier.verify(session, spec, inputs)
                    status["timing_ms"]["verify"] = int((time.monotonic() - phase) * 1000)
                    if evidence.status != "verified" or trace.outcome != "awaiting_verification":
                        status.update(
                            status=evidence.status if evidence.status != "verified" else "failed",
                            evidence=evidence.model_dump(mode="json"),
                        )
                        return status
                    trace.outcome = "success"
                    trace.actions = prefix + trace.actions
                    effects.action_bindings = prior_mapping | effects.action_bindings
                    store.save("effect-bindings.json", effects.action_bindings)
                    store.save("trace.json", trace)
                    phase = time.monotonic()
                    program, binding = compile_ui(
                        trace, spec, site, effects.action_bindings, source_runs=prior_runs
                    )
                    candidate_hash = self.ledger.put(program, binding, task)
                    status["candidate_hash"] = candidate_hash
                    status["timing_ms"]["compile"] = int((time.monotonic() - phase) * 1000)
                else:
                    runtime = LiveRuntime(spec, site, binding, effects, self.verifier)
                    start_at, completed = await runtime.restore(session, program, inputs)
                    result = await Executor(None, emit, runtime=runtime)(
                        program, inputs, env, session, start_at=start_at, completed_before=completed
                    )
                    status["timing_ms"]["execute_including_verify"] = result.wall_ms
                    evidence = runtime.evidence
                    if not result.success:
                        status.update(
                            status="unknown" if runtime.effect_status() == "unknown" else "failed",
                            evidence=evidence.model_dump(mode="json") if evidence else None,
                        )
                        return status
                status.update(status=evidence.status, evidence=evidence.model_dump(mode="json"))
                if evidence.status == "verified":
                    self.journal.complete(effect_scope, task, status["evidence"])
                    await emit("live.outcome.verified", status["evidence"])
        except UnresolvedEffect:
            status.update(status="unknown", error="UnresolvedEffect")
        except Exception as exc:
            if status["status"] == "running":
                status["status"] = "failed"
            status["error"] = store.sanitizer.clean(str(exc))[:1000]
        finally:
            if session:
                usage = {u.response_id: u for u in getattr(session, "model_usage", [])}
                status["usage"] = [u.model_dump(mode="json") for u in usage.values()]
                status["model_calls"] = sum(u.model_calls for u in usage.values())
                status["ui_actions"] = session.ui_actions
                status["http_requests"] = session.http_requests
                status["diagnostics"] = session.diagnostics[-10:]
                try:
                    await session.close()
                except Exception:
                    status["close_error"] = True
            status["wall_ms"] = int((time.monotonic() - started) * 1000)
            private_json(folder / "status.json", status)
            await emit("live.task.finished", status)
        return status

    async def validate(self, workflow_id, profile_id, candidate_hash, rows):
        if len(rows) != 2:
            raise ValueError("validation requires exactly two explicitly supplied canaries")
        contexts = [self.context(workflow_id, profile_id, row) for row in rows]
        spec, site, _ = contexts[0]
        keys = [inputs[spec.task_key_field] for _, _, inputs in contexts]
        _, _, learned = self.ledger.get(candidate_hash)
        if len(set(keys)) != 2 or learned in keys:
            raise ValueError("canaries must use two distinct new task keys")
        scope = self.ledger.scope(spec, site)
        expected_parent = self.ledger.current(scope)
        results = []
        for row in rows:
            result = await self.run(
                workflow_id, profile_id, row, "canary", candidate_hash, _canary=True
            )
            results.append(result)
            if result["status"] != "verified" or result["reused"]:
                return {"status": "inconclusive", "runs": results}
        self.ledger.promote(scope, candidate_hash, results, expected_parent)
        await self.bus.emit(
            results[-1]["run_id"],
            "live.candidate.validated",
            {"candidate_hash": candidate_hash, "scope_hash": scope},
        )
        return {"status": "current", "candidate_hash": candidate_hash, "runs": results}

    async def readback(self, workflow_id, profile_id, rows):
        """Fresh business evidence only: no writes, journal changes, or model calls."""
        if not 1 <= len(rows) <= 20:
            raise ValueError("read-back requires one to twenty fixed inputs")
        contexts = [self.context(workflow_id, profile_id, row) for row in rows]
        spec, site, _ = contexts[0]
        run_id = str(uuid4())
        store = TraceStore(self.root / "runs", run_id)
        session = self.session_factory(
            replace(self.settings, crm_url=site.start_url),
            store,
            run_id,
            "readback",
            site=site,
            profiles=self.profiles,
        )
        results = []
        with self.journal.lease("profile:" + profile_id):
            try:
                await session.open(
                    EnvironmentSpec(
                        app=site.id, revision=digest(site), seed_version="not-applicable"
                    ),
                    "readback",
                )
                for _, _, inputs in contexts:
                    evidence = await self.verifier.verify(session, spec, inputs)
                    results.append(
                        {
                            "task_key": inputs[spec.task_key_field],
                            "status": evidence.status,
                            "evidence": evidence.model_dump(mode="json"),
                            "model_calls": 0,
                        }
                    )
            finally:
                await session.close()
        result = {
            "status": "verified"
            if all(r["status"] == "verified" for r in results)
            else "incomplete",
            "run_id": run_id,
            "read_only": True,
            "runs": results,
        }
        private_json(self.root / "readbacks" / (run_id + ".json"), result)
        return result

    async def batch(self, workflow_id, profile_id, rows, *, batch_id=None):
        if not 1 <= len(rows) <= 5:
            raise ValueError("live batches contain one to five explicitly supplied rows")
        contexts = [self.context(workflow_id, profile_id, row) for row in rows]
        keys = [i[s.task_key_field] for s, _, i in contexts]
        if len(set(keys)) != len(keys):
            raise ValueError("batch task keys must be unique")
        batch_id = batch_id or str(uuid4())
        manifest = {"workflow_id": workflow_id, "profile_id": profile_id, "rows": rows}
        private_json(self.root / "batches" / batch_id / "manifest.json", manifest)
        results = []
        for row in rows:
            result = await self.run(workflow_id, profile_id, row)
            results.append(result)
            await self.bus.emit(
                batch_id,
                "live.batch.progress",
                {"completed": len(results), "total": len(rows), "item": result},
            )
            private_json(self.root / "batches" / batch_id / "result.json", {"runs": results})
            if result["status"] in ("unknown", "needs_user_authentication"):
                break
        return {
            "batch_id": batch_id,
            "runs": results,
            "status": "verified"
            if len(results) == len(rows) and all(r["status"] == "verified" for r in results)
            else "incomplete",
        }
