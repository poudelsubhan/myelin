import asyncio
import json
import time
from collections import defaultdict

from myelin.schema import Program, VersionRow


class CandidateStore:
    def __init__(self, root):
        self.root = root

    def put(self, program):
        digest = program.content_hash()
        folder = self.root / program.workflow / digest
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "program.json"
        if path.exists():
            if path.read_text() != program.canonical_json():
                raise ValueError("immutable candidate corruption")
        else:
            with path.open("x") as f:
                f.write(program.canonical_json())
        return digest

    def get(self, workflow, digest):
        if workflow not in ("crm.create_invoice", "expense.submit_expense"):
            raise ValueError("unsupported workflow")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid content hash")
        program = Program.model_validate_json(
            (self.root / workflow / digest / "program.json").read_text()
        )
        if program.content_hash() != digest:
            raise ValueError("immutable candidate corruption")
        return program


class Ledger:
    def __init__(self, root, candidates):
        self.root, self.candidates = root, candidates
        self.locks = defaultdict(asyncio.Lock)
        root.mkdir(parents=True, exist_ok=True)

    def current(self, workflow):
        path = self.root / f"{workflow}.current.json"
        return json.loads(path.read_text()) if path.exists() else None

    def rows(self, workflow):
        path = self.root / f"{workflow}.jsonl"
        return (
            [VersionRow.model_validate_json(line) for line in path.read_text().splitlines()]
            if path.exists()
            else []
        )

    async def promote(
        self, gate, expected_parent_hash, mode, suite_ids, ceiling=250, baseline=None
    ):
        workflow = gate.request.workflow
        async with self.locks[workflow]:
            current = self.current(workflow)
            current_hash = current["candidate_hash"] if current else None
            program = self.candidates.get(workflow, gate.request.candidate_hash)
            verdict, rule, reason = "promoted", "correctness", "All required cases passed"
            metrics = {
                "max_work_units": gate.max_work_units,
                "oracle_passes": gate.oracle_passes,
                "oracle_total": gate.oracle_total,
            }

            def key(r):
                return (
                    r.workflow,
                    r.environment.model_dump(),
                    r.policy_revision,
                    r.suite_hash,
                )

            complete = (
                gate.status == "passed"
                and len(gate.cases) == len(suite_ids)
                and {c.case_id for c in gate.cases} == set(suite_ids)
                and all(
                    c.program_success and c.assertions and all(a.passed for a in c.assertions)
                    for c in gate.cases
                )
                and gate.oracle_passes == len(suite_ids)
                and gate.oracle_total == len(suite_ids)
            )
            if gate.request.reference_mode != "none":
                complete = (
                    complete
                    and gate.all_references_complete
                    and all(c.reference_status == "success" and c.outcome_match for c in gate.cases)
                )
            if current_hash != expected_parent_hash or program.parent_hash != expected_parent_hash:
                verdict, rule, reason = (
                    "rejected",
                    "stale_parent",
                    "Current/expected/candidate parent differs",
                )
            elif not complete:
                verdict, rule, reason = (
                    "inconclusive" if gate.status == "inconclusive" else "rejected",
                    "correctness",
                    "Missing or failed required case/reference",
                )
            elif (
                gate.request.environment.revision not in program.supported_revisions
                or gate.request.policy_revision != program.policy_revision
            ):
                verdict, rule, reason = (
                    "rejected",
                    "comparison_key",
                    "Unsupported environment or policy",
                )
            elif gate.max_work_units > ceiling:
                verdict, rule, reason = "rejected", "work_ceiling", "Declared work ceiling exceeded"
            elif mode == "initial" and current:
                verdict, rule, reason = "rejected", "initial", "A current program already exists"
            elif mode != "initial" and not current:
                verdict, rule, reason = "rejected", "missing_baseline", "No current program"
            elif mode == "optimization":
                if (
                    baseline is None
                    or key(baseline.request) != key(gate.request)
                    or baseline.status != "passed"
                ):
                    verdict, rule, reason = (
                        "rejected",
                        "comparison_key",
                        "Fresh comparable baseline required",
                    )
                elif gate.max_work_units > baseline.max_work_units:
                    verdict, rule, reason = (
                        "rejected",
                        "non_regression",
                        "Maximum work increased; zero slack",
                    )
            elif mode == "restoration":
                if current["environment_revision"] == gate.request.environment.revision:
                    verdict, rule, reason = (
                        "rejected",
                        "restoration",
                        "Environment contract did not change",
                    )
            elif mode == "policy_change":
                if current["policy_revision"] == gate.request.policy_revision:
                    verdict, rule, reason = "rejected", "policy_change", "Policy did not change"
            row = VersionRow(
                candidate_hash=program.content_hash(),
                parent_hash=program.parent_hash,
                version=program.version,
                environment_revision=gate.request.environment.revision,
                policy_revision=gate.request.policy_revision,
                suite_hash=gate.request.suite_hash,
                gate_id=gate.gate_id,
                mode=mode,
                verdict=verdict,
                metrics_before=current,
                metrics_after=metrics,
                rule=rule,
                reason=reason,
                timestamp=time.time(),
            )
            with (self.root / f"{workflow}.jsonl").open("a") as f:
                f.write(row.model_dump_json() + "\n")
                f.flush()
            if verdict == "promoted":
                path = self.root / f"{workflow}.current.json"
                temp = path.with_suffix(".tmp")
                temp.write_text(row.model_dump_json())
                temp.replace(path)
            return row
