from myelin.gate.gate import GateRunner, suite, suite_hash
from myelin.program.compiler import Compiler
from myelin.program.lifter import lift
from myelin.schema import GateRequest, NetworkEvent


class Pipeline:
    def __init__(self, settings, candidates, ledger, run_program, emit):
        self.settings, self.candidates, self.ledger = settings, candidates, ledger
        self.run_program, self.emit = run_program, emit

    async def compile_and_promote(self, trace, store):
        network = [
            NetworkEvent.model_validate_json(line)
            for line in (store.folder / "network.jsonl").read_text().splitlines()
        ]
        http_candidates = lift(trace, trace.inputs, network)
        store.save("http-candidates.json", [c.model_dump(mode="json") for c in http_candidates])
        current = self.ledger.current(trace.workflow)
        prior = self.candidates.get(trace.workflow, current["candidate_hash"]) if current else None
        program = await Compiler(self.settings, store, self.emit)(
            trace, http_candidates, prior=prior
        )
        digest = self.candidates.put(program)
        request = GateRequest(
            candidate_hash=digest,
            workflow=trace.workflow,
            environment=trace.environment,
            policy_revision=program.policy_revision,
            suite_hash=suite_hash(trace.workflow, program.policy_revision),
            reference_mode="none",
            reference_case_ids=[],
        )
        runner = GateRunner(
            self.settings.runs_dir / "gates", self.candidates, self.run_program, self.emit
        )
        gate = await runner(request)
        baseline = None
        if prior:
            baseline = await runner(
                request.model_copy(update={"candidate_hash": prior.content_hash()})
            )
        row = await self.ledger.promote(
            gate,
            prior.content_hash() if prior else None,
            "optimization" if prior else "initial",
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
