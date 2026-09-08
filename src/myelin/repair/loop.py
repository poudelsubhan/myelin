import time
from decimal import Decimal

from myelin.gate.gate import GateRunner, suite, suite_hash
from myelin.program.compiler import Compiler
from myelin.program.executor import Executor
from myelin.program.lifter import lift
from myelin.repair.reconcile import reconcile
from myelin.schema import GateRequest, NetworkEvent, Trace
from myelin.verification.oracle import verify
from myelin.vision.harness import Recorder


class RepairLoop:
    def __init__(
        self, settings, admin, candidates, ledger, gate_case, emit, run_reference=None, outcome=None
    ):
        self.settings, self.admin, self.candidates, self.ledger = (
            settings,
            admin,
            candidates,
            ledger,
        )
        self.gate_case, self.emit = gate_case, emit
        self.run_reference, self.outcome = run_reference, outcome

    async def run(self, program, inputs, environment, session, full=False):
        started = time.monotonic()
        before = await self.admin.state(session.tenant)
        result = await Executor(self.admin, self.emit)(
            program, inputs, environment, session, allow_drift=True
        )
        if result.success:
            return result, None, None
        failure = result.failure
        if failure is None:
            return result, None, None
        await self.emit(
            "repair.started",
            {
                "failed_step_id": result.failed_step_id,
                "completed_step_ids": failure.checkpoint.completed_step_ids,
                "failure": failure.model_dump(mode="json"),
            },
        )
        session.store.save("failure-before-repair.json", result)
        reconciled = await reconcile(session, failure)
        await self.emit("repair.reconciled", reconciled)
        if reconciled["status"] == "unknown":
            return result, None, None
        index = next(i for i, s in enumerate(program.steps) if s.id == result.failed_step_id)
        if reconciled["status"] == "applied":
            if reconciled["result"] and "location" in reconciled["result"]:
                session.variables["invoice_location"] = reconciled["result"]["location"]
            if environment.app == "expense" and reconciled["result"]:
                from myelin.program.bindings import json_path

                for rule in getattr(program.steps[index], "extract", []):
                    if rule.source == "json_path":
                        target = session.secrets if rule.secret else session.variables
                        target[rule.target_var] = json_path(reconciled["result"], rule.expression)
            resumed = await Executor(self.admin, self.emit)(
                program,
                inputs,
                environment,
                session,
                allow_drift=True,
                start_at=index + 1,
                completed_before=failure.checkpoint.completed_step_ids + [result.failed_step_id],
                before_state=before,
            )
            await self.emit(
                "repair.finished", {"reconciled_only": True, "success": resumed.success}
            )
            return resumed, None, None
        resume = failure.checkpoint.resume_url
        if not resume:
            return result, None, None
        request = next(
            (
                e
                for e in reversed(session.network)
                if e.action_id == result.failed_step_id and e.method == "POST"
            ),
            None,
        )
        if request:
            session.repair_identity = {
                "url": request.url,
                "operation_id": failure.checkpoint.operation_id,
            }
        if environment.app == "expense":
            from myelin.adapters.expense import resume as resume_expense

            await resume_expense(session, resume)
        await session.perform("repair-resume", "navigate", None, {"url": resume})
        if request is None and environment.app == "crm":
            from urllib.parse import urljoin

            from myelin.program.bindings import BindingError, html_input

            for observed in reversed(session.network):
                if observed.url == resume and isinstance(observed.response_body, str):
                    try:
                        action_url = html_input(observed.response_body, "action_url")
                        session.repair_identity = {
                            "url": urljoin(resume, action_url),
                            "operation_id": failure.checkpoint.operation_id,
                        }
                        break
                    except BindingError:
                        continue
        remaining = (
            "The original task failed at: "
            + program.steps[index].intent
            + ". Earlier completed step IDs: "
            + str(failure.checkpoint.completed_step_ids)
            + ". Continue in this retained authenticated session from the current form. "
            "Finish only the remaining original task. If its invoice/expense already exists, "
            "complete payment/submission for that entity; never create it again. "
            "Do not repeat login or earlier writes. "
            "The prior request was rejected before applying a write. The app form may have changed."
        )
        trace = await Recorder(self.settings, self.emit)(
            program.workflow,
            inputs,
            environment,
            session=session,
            checkpoint=failure.checkpoint,
            remaining_goal=remaining,
            policy_revision=program.policy_revision,
        )
        session.repair_identity = None
        after = await self.admin.state(session.tenant)
        assertions = verify(
            program.workflow, before, after, inputs, {"revision": program.policy_revision}
        )
        result.success = trace.outcome == "awaiting_verification" and all(
            a.passed for a in assertions
        )
        result.status = "completed" if result.success else "failed"
        result.oracle_assertions = assertions
        result.final_observation = trace.final_observation or trace.initial_observation
        result.model_calls = len(session.model_usage)
        result.model_usd = (
            str(sum(Decimal(u.usd) for u in session.model_usage))
            if all(u.usd is not None for u in session.model_usage)
            else None
        )
        result.wall_ms = int((time.monotonic() - started) * 1000)
        result.http_requests = session.http_requests
        result.ui_actions = session.ui_actions
        result.work_units = session.http_requests + 10 * session.ui_actions
        if not result.success:
            await self.emit("repair.finished", {"success": False, "candidate_promoted": False})
            return result, None, None
        # Merge permitted source provenance; runtime app code/DB/admin are never compiler inputs.
        new_network = [
            NetworkEvent.model_validate_json(line)
            for line in (session.store.folder / "network.jsonl").read_text().splitlines()
        ]
        http_candidates = lift(trace, inputs, new_network)
        prior_actions = []
        prior_marks = []
        for run_id in program.compiled_from:
            root = self.settings.runs_dir / run_id
            if not (root / "trace.json").exists():
                continue
            prior_trace = Trace.model_validate_json((root / "trace.json").read_text())
            prior_actions.extend(prior_trace.actions)
            prior_marks.extend(prior_trace.steer_marks)
            net = [
                NetworkEvent.model_validate_json(line)
                for line in (root / "network.jsonl").read_text().splitlines()
            ]
            http_candidates.extend(lift(prior_trace, prior_trace.inputs, net))
        trace.actions = list({a.id: a for a in prior_actions + trace.actions}.values())
        trace.steer_marks = list({m.id: m for m in prior_marks + trace.steer_marks}.values())
        trace.outcome = "success"
        session.store.save("repair-merged-trace.json", trace)
        session.store.save("result.json", result)
        try:
            candidate = await Compiler(self.settings, session.store, self.emit)(
                trace, http_candidates, prior=program, repair_scope=[result.failed_step_id]
            )
        except Exception as exc:
            await self.emit(
                "repair.finished",
                {
                    "success": result.success,
                    "candidate_promoted": False,
                    "candidate_error": type(exc).__name__,
                },
            )
            return result, None, None
        digest = self.candidates.put(candidate)
        request = GateRequest(
            candidate_hash=digest,
            workflow=program.workflow,
            environment=environment,
            policy_revision=program.policy_revision,
            suite_hash=suite_hash(program.workflow, program.policy_revision),
            reference_mode="full" if full else "none",
            reference_case_ids=[c.case_id for c in suite(program.workflow)] if full else [],
        )
        gate = await GateRunner(
            self.settings.runs_dir / "gates",
            self.candidates,
            self.gate_case,
            self.emit,
            self.run_reference,
            self.outcome,
        )(request)
        row = await self.ledger.promote(
            gate,
            program.content_hash(),
            "restoration",
            [c.case_id for c in suite(program.workflow)],
        )
        await self.emit("ledger.updated", row.model_dump(mode="json"))
        await self.emit(
            "repair.finished",
            {
                "success": result.success,
                "candidate_promoted": row.verdict == "promoted",
                "candidate_hash": digest,
                "gate_id": gate.gate_id,
                "failed_step_id": result.failed_step_id,
            },
        )
        return result, candidate, gate
