"""Actual boundary and duplicate-submission gates must reject before promotion."""

import asyncio
import json
from uuid import uuid4

import httpx

from myelin.config import ROOT, Settings
from myelin.gate.gate import suite_hash
from myelin.gate.ledger import CandidateStore
from myelin.schema import Program


async def main():
    settings = Settings.load()
    workflow = "expense.submit_expense"
    root = settings.runs_dir / ("phase6-rule-rejections-" + str(uuid4()))
    root.mkdir(parents=True)
    evidence = {"run_id": root.name, "cases": {}}
    async with httpx.AsyncClient(base_url="http://localhost:8100", timeout=60) as client:

        async def post(path, body):
            r = await client.post(path, json=body)
            r.raise_for_status()
            return r.json()

        current = (await client.get("/ledger/" + workflow)).json()["current"]
        original = CandidateStore(ROOT / "programs").get(workflow, current["candidate_hash"])
        manifest = json.loads(
            (settings.runs_dir / "gates" / current["gate_id"] / "manifest.json").read_text()
        )
        for label in ("wrong_comparator", "duplicate_submit"):
            candidate = original.model_copy(deep=True)
            candidate.parent_hash = original.content_hash()
            candidate.version += 1
            if label == "wrong_comparator":
                next(s for s in candidate.steps if s.kind == "branch").condition.op = "ge"
            else:
                extra = candidate.steps[-1].model_copy(deep=True)
                extra.id = "duplicate-submit"
                extra.operation_key = "duplicate-submit"
                extra.depends_on = [candidate.steps[-1].id]
                candidate.steps.append(extra)
            candidate = Program.model_validate_json(candidate.model_dump_json())
            digest = (await post("/candidates", {"program": candidate.model_dump(mode="json")}))[
                "candidate_hash"
            ]
            gid = (
                await post(
                    "/gates",
                    {
                        "request": {
                            "workflow": workflow,
                            "candidate_hash": digest,
                            "environment": manifest["environment"],
                            "policy_revision": original.policy_revision,
                            "suite_hash": suite_hash(workflow, original.policy_revision),
                            "reference_mode": "none",
                            "reference_case_ids": [],
                        }
                    },
                )
            )["gate_id"]
            async with asyncio.timeout(180):
                while True:
                    gate = (await client.get("/gates/" + gid)).json()
                    if gate["status"] not in ("running", "pending"):
                        break
                    await asyncio.sleep(1)
            assert gate["status"] == "failed", gate
            if label == "wrong_comparator":
                assert not next(c for c in gate["cases"] if c["case_id"] == "expense-2")[
                    "program_success"
                ]
            decision = await post(
                "/promotions",
                {
                    "gate_id": gid,
                    "expected_parent_hash": original.content_hash(),
                    "mode": "optimization",
                },
            )
            assert decision["verdict"] == "rejected", decision
            assert (await client.get("/ledger/" + workflow)).json()["current"][
                "candidate_hash"
            ] == original.content_hash()
            evidence["cases"][label] = {
                "gate_id": gid,
                "candidate_hash": digest,
                "decision": decision,
            }
        evidence["passed"] = True
        (root / "evidence.json").write_text(json.dumps(evidence, indent=2))
        print(json.dumps(evidence), flush=True)
    return evidence


if __name__ == "__main__":
    asyncio.run(main())
