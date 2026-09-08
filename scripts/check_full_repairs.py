"""Real expense contract and CRM UI-consent repair gates, with isolated tenants."""

import argparse
import asyncio
import json
from uuid import uuid4

import httpx

from myelin.adapters import crm, expense
from myelin.config import ROOT, Settings
from myelin.gate.gate import suite_hash
from myelin.gate.ledger import CandidateStore
from myelin.schema import Compare, LiteralRef, NamedRef, Trace, UiStep


async def main(which):
    settings = Settings.load()
    root = settings.runs_dir / ("phase6-repair-" + which + "-" + str(uuid4()))
    root.mkdir(parents=True)
    evidence = {"run_id": root.name, "app": which}
    candidates = CandidateStore(ROOT / "programs")
    workflow = "crm.create_invoice" if which == "crm" else "expense.submit_expense"
    async with httpx.AsyncClient(base_url="http://localhost:8100", timeout=240) as client:

        async def post(path, data, admin=False):
            r = await client.post(
                path,
                json=data,
                headers={"X-Myelin-Demo-Token": settings.demo_token} if admin else {},
            )
            r.raise_for_status()
            return r.json()

        async def wait(path):
            async with asyncio.timeout(1200):
                while True:
                    r = (await client.get(path)).json()
                    if r["status"] not in ("running", "pending"):
                        return r
                    await asyncio.sleep(1)

        if which == "crm":
            # The hybrid baseline retains an observed UI payment action on purpose.
            current = (await client.get("/ledger/" + workflow)).json()["current"]
            if not current:
                raise ValueError("Run the core demo first to record a fresh CRM source trace")
            source = candidates.get(workflow, current["candidate_hash"])
            while "crm-v1" not in source.supported_revisions:
                if not source.parent_hash:
                    raise ValueError("No normal-revision CRM ancestor; run the core demo first")
                source = candidates.get(workflow, source.parent_hash)
            traces = [
                Trace.model_validate_json((settings.runs_dir / rid / "trace.json").read_text())
                for rid in source.compiled_from
            ]
            action = next(
                a
                for trace in traces
                for a in trace.actions
                if a.target and a.target.value == "Mark paid" and a.outcome == "success"
            )
            ui = UiStep(
                id="payment-ui",
                intent="Mark the existing invoice paid through its observed UI",
                source_action_ids=[action.id],
                pre=[
                    Compare(
                        source="business",
                        path="$.invoices[0].status",
                        op="eq",
                        expected=LiteralRef(value="unpaid"),
                    )
                ],
                post=[
                    Compare(
                        source="business",
                        path="$.invoices[0].status",
                        op="eq",
                        expected=LiteralRef(value="paid"),
                    )
                ],
                effect="write",
                operation_key="payment-ui",
                depends_on=["invoice-write"],
                resume_url=NamedRef(kind="variable", key="invoice_location"),
                action="click",
                target=action.target,
                arguments={},
            )
            program = source.model_copy(
                update={
                    "steps": source.steps[
                        : next(
                            i for i, step in enumerate(source.steps) if step.id == "invoice-write"
                        )
                        + 1
                    ]
                    + [ui],
                    "parent_hash": None,
                    "version": 1,
                    "notes": "Hybrid baseline retains recorded UI payment for consent repair.",
                }
            )
            await post("/demo/reset-ledger", {"workflow": workflow}, True)
            digest = (await post("/candidates", {"program": program.model_dump(mode="json")}))[
                "candidate_hash"
            ]
            environment = {"app": "crm", "revision": "crm-v1", "seed_version": "crm-seed-v1"}
            started = await post(
                "/gates",
                {
                    "request": {
                        "candidate_hash": digest,
                        "workflow": workflow,
                        "environment": environment,
                        "policy_revision": program.policy_revision,
                        "suite_hash": suite_hash(),
                        "reference_mode": "none",
                        "reference_case_ids": [],
                    }
                },
            )
            baseline = await wait("/gates/" + started["gate_id"])
            assert baseline["status"] == "passed", baseline
            decision = await post(
                "/promotions",
                {"gate_id": started["gate_id"], "expected_parent_hash": None, "mode": "initial"},
            )
            assert decision["verdict"] == "promoted"
            inputs = {
                "customer": "Birch Studio",
                "description": "UI consent 'repair'",
                "quantity": 2,
                "unit_price_cents": 3456,
                "due_date": "2026-11-30",
            }
            mutations = ["add_modal:consent"]
            environment.update(mutations=mutations, revision=crm.revision(mutations))
            evidence["baseline_gate"] = started["gate_id"]
        else:
            current = (await client.get("/ledger/" + workflow)).json()["current"]
            digest = current["candidate_hash"]
            program = candidates.get(workflow, digest)
            mutations = ["rename_field:amount"]
            environment = {
                "app": "expense",
                "revision": expense.revision(mutations),
                "mutations": mutations,
                "seed_version": "expense-seed-v1",
            }
            inputs = {
                "merchant": "Repair 'amount' merchant",
                "amount_cents": 50001,
                "category": "Supplies",
                "date": "2026-09-08",
                "receipt_text": "Repair receipt",
            }
        started = await post(
            "/runs",
            {
                "workflow": workflow,
                "mode": "repair",
                "program_hash": digest,
                "inputs": inputs,
                "environment": environment,
                "policy_revision": program.policy_revision,
                "full_validation": True,
            },
        )
        print(json.dumps({"proof": root.name, "repair_run": started["run_id"]}), flush=True)
        status = await wait("/runs/" + started["run_id"])
        evidence.update(repair_run_id=started["run_id"], repair=status, original_hash=digest)
        (root / "evidence.json").write_text(json.dumps(evidence, indent=2))
        assert (
            status["status"] == "completed"
            and status.get("candidate_hash") != digest
            and status.get("gate_id")
        ), status
        gate = (await client.get("/gates/" + status["gate_id"])).json()
        assert gate["status"] == "passed" and gate["reference_passes"] == 8, gate
        replay = await post(
            "/runs",
            {
                "workflow": workflow,
                "mode": "program",
                "program_hash": status["candidate_hash"],
                "inputs": inputs
                | (
                    {"description": "Post-consent new input"}
                    if which == "crm"
                    else {"merchant": "Post-repair new input"}
                ),
                "environment": environment,
                "policy_revision": program.policy_revision,
            },
        )
        replay_status = await wait("/runs/" + replay["run_id"])
        assert (
            replay_status["status"] == "completed" and replay_status["result"]["model_calls"] == 0
        ), replay_status
        evidence.update(
            passed=True,
            replay_run_id=replay["run_id"],
            candidate_hash=status["candidate_hash"],
            gate_id=status["gate_id"],
        )
        (root / "evidence.json").write_text(json.dumps(evidence, indent=2))
        print(json.dumps({k: v for k, v in evidence.items() if k != "repair"}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("app", choices=["crm", "expense"])
    asyncio.run(main(parser.parse_args().app))
