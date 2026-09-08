import asyncio
import hashlib
import json
import os
import statistics
from uuid import uuid4

from myelin.config import ROOT
from myelin.schema import AssertionResult, CaseResult, CaseSpec, GateResult


def suite(workflow="crm.create_invoice"):
    if workflow != "crm.create_invoice":
        raise ValueError("unsupported suite")
    raw = json.loads((ROOT / "src/myelin/workflows/crm-cases.json").read_text())
    return [CaseSpec.model_validate(c) for c in raw]


def suite_hash(workflow="crm.create_invoice", policy="crm-policy-v1"):
    payload = {"policy": policy, "cases": [c.model_dump() for c in suite(workflow)]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class GateRunner:
    def __init__(self, root, candidates, run_program, emit):
        self.root, self.candidates = root, candidates
        self.run_program, self.emit = run_program, emit

    async def __call__(self, request, gate_id=None):
        request = request.model_copy(deep=True)
        gate_id = gate_id or str(uuid4())
        cases = suite(request.workflow)
        if request.suite_hash != suite_hash(request.workflow, request.policy_revision):
            raise ValueError("gate suite is not the locked policy suite")
        if request.reference_mode != "none":
            raise ValueError("model-reference validation requires Phase 5")
        if request.reference_case_ids:
            raise ValueError("reference mode none requires no reference IDs")
        program = self.candidates.get(request.workflow, request.candidate_hash)
        folder = self.root / gate_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "manifest.json").write_text(request.model_dump_json(indent=2))
        await self.emit("gate.started", {"gate_id": gate_id, **request.model_dump(mode="json")})
        semaphore = asyncio.Semaphore(int(os.getenv("MYELIN_GATE_CONCURRENCY", "2")))

        async def run_case(case):
            async with semaphore:
                run_id = str(uuid4())
                try:
                    result = await self.run_program(
                        program, case.inputs, request.environment, run_id
                    )
                    assertions = result.oracle_assertions + [
                        AssertionResult(
                            name="deterministic.zero_model_calls",
                            expected=0,
                            observed=result.model_calls,
                            passed=result.model_calls == 0,
                        )
                    ]
                    passed = result.success and all(a.passed for a in assertions)
                    work, wall = result.work_units, result.wall_ms
                except Exception as exc:
                    assertions = [
                        AssertionResult(
                            name="execution.completed",
                            passed=False,
                            expected="completed",
                            observed=type(exc).__name__,
                        )
                    ]
                    passed, work, wall = False, 0, 0
                row = CaseResult(
                    case_id=case.case_id,
                    program_run_id=run_id,
                    program_success=passed,
                    assertions=assertions,
                    reference_run_id=None,
                    reference_status="not_requested",
                    outcome_match=None,
                    differences=[],
                    program_work_units=work,
                    program_wall_ms=wall,
                )
                with (folder / "cases.jsonl").open("a") as f:
                    f.write(row.model_dump_json() + "\n")
                await self.emit("gate.case", {"gate_id": gate_id, **row.model_dump(mode="json")})
                return row

        results = await asyncio.gather(*(run_case(c) for c in cases))
        # Re-read immutable bytes to catch tampering while workers were executing.
        self.candidates.get(request.workflow, request.candidate_hash)
        passed = sum(c.program_success for c in results)
        result = GateResult(
            gate_id=gate_id,
            request=request,
            status="passed" if passed == len(cases) else "failed",
            cases=results,
            oracle_passes=passed,
            oracle_total=len(cases),
            reference_passes=0,
            reference_total=0,
            all_references_complete=True,
            model_usage=[],
            median_wall_ms=int(statistics.median(c.program_wall_ms for c in results)),
            max_work_units=max(c.program_work_units for c in results),
        )
        (folder / "result.json").write_text(result.model_dump_json(indent=2))
        await self.emit("gate.finished", result.model_dump(mode="json"))
        return result
