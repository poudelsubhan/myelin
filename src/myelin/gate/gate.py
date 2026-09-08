import asyncio
import hashlib
import json
import os
import statistics
from uuid import uuid4

from myelin.config import ROOT
from myelin.gate.coverage import branch_coverage
from myelin.schema import AssertionResult, CaseResult, CaseSpec, GateResult


def suite(workflow="crm.create_invoice"):
    names = {"crm.create_invoice": "crm", "expense.submit_expense": "expense"}
    if workflow not in names:
        raise ValueError("unsupported suite")
    raw = json.loads((ROOT / f"src/myelin/workflows/{names[workflow]}-cases.json").read_text())
    return [CaseSpec.model_validate(c) for c in raw]


def suite_hash(workflow="crm.create_invoice", policy="crm-policy-v1"):
    payload = {"policy": policy, "cases": [c.model_dump() for c in suite(workflow)]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class GateRunner:
    def __init__(self, root, candidates, run_program, emit, run_reference=None, outcome=None):
        self.root, self.candidates = root, candidates
        self.run_program, self.emit = run_program, emit
        self.run_reference, self.outcome = run_reference, outcome

    async def __call__(self, request, gate_id=None):
        request = request.model_copy(deep=True)
        gate_id = gate_id or str(uuid4())
        cases = suite(request.workflow)
        if request.suite_hash != suite_hash(request.workflow, request.policy_revision):
            raise ValueError("gate suite is not the locked policy suite")
        case_ids = {c.case_id for c in cases}
        ref_ids = set(request.reference_case_ids)
        if len(ref_ids) != len(request.reference_case_ids) or not ref_ids <= case_ids:
            raise ValueError("reference coverage contains duplicate/unknown cases")
        if request.reference_mode == "full" and ref_ids != case_ids:
            raise ValueError("full reference coverage requires all eight case IDs")
        if request.reference_mode == "none" and ref_ids:
            raise ValueError("reference mode none requires no reference IDs")
        if request.reference_mode != "none" and (
            not self.run_reference or not self.outcome or not ref_ids
        ):
            raise ValueError("reference provider and locked reference coverage required")
        if any(c.seed_version != request.environment.seed_version for c in cases):
            raise ValueError("case seed differs from requested environment")
        model_usage = []
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
                reference_run_id, reference_status, outcome_match = None, "not_requested", None
                differences = []
                if case.case_id in ref_ids:
                    reference_run_id = str(uuid4())
                    try:
                        reference, ref_projection, usage_ids = await self.run_reference(
                            program, case.inputs, request.environment, reference_run_id
                        )
                        model_usage.extend(usage_ids)
                        reference_status = (
                            "success"
                            if reference.success
                            and reference.model_calls > 0
                            and reference.oracle_assertions
                            and all(a.passed for a in reference.oracle_assertions)
                            else "failure"
                        )
                        program_projection = await self.outcome(program, case.inputs, run_id)
                        outcome_match = program_projection == ref_projection
                        differences = [
                            AssertionResult(
                                name="reference.business_projection",
                                expected=ref_projection,
                                observed=program_projection,
                                passed=outcome_match,
                            )
                        ]
                    except Exception as exc:
                        reference_status, outcome_match = "failure", False
                        differences = [
                            AssertionResult(
                                name="reference.completed",
                                expected="completed",
                                observed=type(exc).__name__,
                                passed=False,
                            )
                        ]
                row = CaseResult(
                    case_id=case.case_id,
                    program_run_id=run_id,
                    program_success=passed,
                    assertions=assertions,
                    reference_run_id=reference_run_id,
                    reference_status=reference_status,
                    outcome_match=outcome_match,
                    differences=differences,
                    program_work_units=work,
                    program_wall_ms=wall,
                )
                with (folder / "cases.jsonl").open("a") as f:
                    f.write(row.model_dump_json() + "\n")
                await self.emit("gate.case", {"gate_id": gate_id, **row.model_dump(mode="json")})
                return row

        results = await asyncio.gather(*(run_case(c) for c in cases))
        # Failed references still incurred usage; retain every observed response ID.
        for case in results:
            if case.reference_run_id:
                usage = self.root.parent / case.reference_run_id / "usage.jsonl"
                if usage.exists():
                    model_usage.extend(
                        json.loads(line)["response_id"] for line in usage.read_text().splitlines()
                    )
        # Re-read immutable bytes to catch tampering while workers were executing.
        self.candidates.get(request.workflow, request.candidate_hash)
        coverage = branch_coverage(program, results, self.root.parent)
        (folder / "coverage.json").write_text(json.dumps(coverage, indent=2))
        coverage_complete = all(row["true"] and row["false"] for row in coverage.values())
        passed = sum(c.program_success for c in results)
        ref_passes = sum(
            c.reference_status == "success" and c.outcome_match is True for c in results
        )
        refs_complete = all(
            c.reference_status == "success" for c in results if c.case_id in ref_ids
        )
        result = GateResult(
            gate_id=gate_id,
            request=request,
            status="passed"
            if passed == len(cases) and ref_passes == len(ref_ids) and coverage_complete
            else "failed",
            cases=results,
            oracle_passes=passed,
            oracle_total=len(cases),
            reference_passes=ref_passes,
            reference_total=len(ref_ids),
            all_references_complete=refs_complete,
            model_usage=list(dict.fromkeys(model_usage)),
            median_wall_ms=int(statistics.median(c.program_wall_ms for c in results)),
            max_work_units=max(c.program_work_units for c in results),
        )
        (folder / "result.json").write_text(result.model_dump_json(indent=2))
        await self.emit("gate.finished", result.model_dump(mode="json"))
        return result
