from myelin.gate.gate import GateRunner, suite, suite_hash
from myelin.program.compiler import Compiler
from myelin.program.lifter import lift
from myelin.schema import GateRequest, NetworkEvent, Trace


class Pipeline:
    def __init__(
        self, settings, candidates, ledger, run_program, emit, run_reference=None, outcome=None
    ):
        self.settings, self.candidates, self.ledger = settings, candidates, ledger
        self.run_program, self.emit = run_program, emit
        self.run_reference, self.outcome = run_reference, outcome

    async def compile_and_promote(self, trace, store, full=False, tool_provider=None):
        network = [
            NetworkEvent.model_validate_json(line)
            for line in (store.folder / "network.jsonl").read_text().splitlines()
        ]
        http_candidates = lift(trace, trace.inputs, network)
        store.save("http-candidates.json", [c.model_dump(mode="json") for c in http_candidates])
        current = self.ledger.current(trace.workflow)
        prior = self.candidates.get(trace.workflow, current["candidate_hash"]) if current else None
        compiler_trace = trace
        policy_change = prior and prior.policy_revision != trace.policy_revision
        if policy_change:
            compiler_trace = trace.model_copy(deep=True)
            for run_id in prior.compiled_from:
                folder = self.settings.runs_dir / run_id
                if (folder / "trace.json").exists():
                    previous = Trace.model_validate_json((folder / "trace.json").read_text())
                    compiler_trace.actions = previous.actions + compiler_trace.actions
                    previous_network = [
                        NetworkEvent.model_validate_json(line)
                        for line in (folder / "network.jsonl").read_text().splitlines()
                    ]
                    http_candidates.extend(lift(previous, previous.inputs, previous_network))
            compiler_trace.actions = list({a.id: a for a in compiler_trace.actions}.values())
            store.save("compiler-trace.json", compiler_trace)
        program = await Compiler(self.settings, store, self.emit)(
            compiler_trace, http_candidates, prior=prior, tool_provider=tool_provider
        )
        if policy_change and trace.environment.app == "expense":
            old = {s.id: s for s in prior.steps}
            new = {s.id: s for s in program.steps}
            for sid in ("expense-login", "expense-create"):
                if old.get(sid) != new.get(sid):
                    raise ValueError("policy update changed an unrelated prefix step")
        digest = self.candidates.put(program)
        request = GateRequest(
            candidate_hash=digest,
            workflow=trace.workflow,
            environment=trace.environment,
            policy_revision=program.policy_revision,
            suite_hash=suite_hash(trace.workflow, program.policy_revision),
            reference_mode="full" if full else "none",
            reference_case_ids=[c.case_id for c in suite(trace.workflow)] if full else [],
        )
        runner = GateRunner(
            self.settings.runs_dir / "gates",
            self.candidates,
            self.run_program,
            self.emit,
            self.run_reference,
            self.outcome,
        )
        gate = await runner(request)
        baseline = None
        mode = (
            "initial"
            if not prior
            else (
                "policy_change"
                if prior.policy_revision != program.policy_revision
                else "optimization"
            )
        )
        if prior and mode == "optimization":
            baseline = await runner(
                request.model_copy(update={"candidate_hash": prior.content_hash()})
            )
        row = await self.ledger.promote(
            gate,
            prior.content_hash() if prior else None,
            mode,
            [c.case_id for c in suite(trace.workflow)],
            baseline=baseline,
        )
        await self.emit("ledger.updated", row.model_dump(mode="json"))
        store.save(
            "pipeline.json",
            {
                "candidate_hash": digest,
                "gate_id": gate.gate_id,
                "verdict": row.verdict,
                "reason": row.reason,
            },
        )
        return program, gate, row
