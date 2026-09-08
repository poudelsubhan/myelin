"""Server-owned, repeatable demo gates. A core run never claims full completion."""

import argparse
import asyncio
import json
import time
from uuid import uuid4

import httpx

from myelin.adapters.crm import revision
from myelin.config import Settings
from myelin.gate.gate import suite_hash
from myelin.schema import LiteralRef, Program
from myelin.verification.demo_audit import audit_core

INPUT = {
    "customer": "Acme Labs",
    "description": "Demo learning invoice",
    "quantity": 3,
    "unit_price_cents": 1999,
    "due_date": "2026-09-30",
}
NINTH = {
    "customer": "Delta Works",
    "description": "New input 'quoted' café",
    "quantity": 4,
    "unit_price_cents": 12345,
    "due_date": "2026-11-30",
}


def with_redundant_fill(program):
    candidate = program.model_copy(deep=True)
    index = next(
        i for i, step in enumerate(candidate.steps) if step.kind == "ui" and step.action == "fill"
    )
    extra = candidate.steps[index].model_copy(deep=True)
    extra.id = "redundant-fill"
    if extra.operation_key:
        extra.operation_key = "redundant-fill"
    candidate.steps.insert(index + 1, extra)
    return Program.model_validate_json(candidate.model_dump_json())


class Demo:
    def __init__(self, stage=False):
        self.settings = Settings.load()
        self.id = "demo-" + str(uuid4())
        self.folder = self.settings.runs_dir / self.id
        self.folder.mkdir(parents=True)
        self.evidence = {
            "demo_id": self.id,
            "mode": "core",
            "started": time.time(),
            "criteria": {},
            "runs": {},
        }
        self.client = httpx.AsyncClient(base_url="http://localhost:8100", timeout=240)
        self.stage = stage

    def beat(self, text):
        print(text, flush=True)
        if self.stage:
            input("Press Enter to continue...")

    async def post(self, path, body=None, internal=False):
        headers = {"X-Myelin-Demo-Token": self.settings.demo_token} if internal else {}
        response = await self.client.post(path, json=body, headers=headers)
        response.raise_for_status()
        return response.json()

    async def wait(self, path):
        for _ in range(600):
            result = (await self.client.get(path)).json()
            if result["status"] not in ("running", "pending"):
                return result
            await asyncio.sleep(1)
        raise TimeoutError(path)

    async def run(self, label, mode, digest=None, mutations=None, inputs=None, lost=None):
        mutations = mutations or []
        request = {
            "workflow": "crm.create_invoice",
            "mode": mode,
            "inputs": inputs or NINTH,
            "program_hash": digest,
            "environment": {
                "app": "crm",
                "revision": revision(mutations),
                "mutations": mutations,
                "seed_version": "crm-seed-v1",
            },
            "lost_response_step": lost,
        }
        started = await self.post("/runs", request, internal=bool(lost))
        result = await self.wait("/runs/" + started["run_id"])
        self.evidence["runs"][label] = {"run_id": started["run_id"], **result}
        self.save()
        assert result["status"] == "completed", (label, result.get("error"))
        assert result["result"]["success"], label
        return result

    async def gate(self, program, environment):
        created = await self.post("/candidates", {"program": program.model_dump(mode="json")})
        started = await self.post(
            "/gates",
            {
                "request": {
                    "candidate_hash": created["candidate_hash"],
                    "workflow": program.workflow,
                    "environment": environment,
                    "policy_revision": program.policy_revision,
                    "suite_hash": suite_hash(),
                    "reference_mode": "none",
                    "reference_case_ids": [],
                }
            },
        )
        gate = await self.wait("/gates/" + started["gate_id"])
        decision = await self.post(
            "/promotions",
            {
                "gate_id": started["gate_id"],
                "expected_parent_hash": program.parent_hash,
                "mode": "optimization",
            },
        )
        return gate, decision

    def save(self):
        (self.folder / "evidence.json").write_text(json.dumps(self.evidence, indent=2))

    async def core(self):
        try:
            await self.post("/demo/reset-ledger", internal=True)
            self.beat("Learn: Astra performs a task, compiles it, and validates eight fresh cases.")
            learned = await self.run("learn", "record", inputs=INPUT)
            original = learned["candidate_hash"]
            assert original and learned["gate_id"]
            program = Program.model_validate_json(
                (
                    self.settings.runs_dir.parent
                    / "programs"
                    / "crm.create_invoice"
                    / original
                    / "program.json"
                ).read_text()
            )
            gate = (await self.client.get("/gates/" + learned["gate_id"])).json()
            self.evidence["criteria"]["C1"] = learned["result"]["model_calls"] > 0
            self.evidence["criteria"]["C2"] = any(
                s.kind == "http" and s.effect == "write" for s in program.steps
            )
            self.evidence["criteria"]["C3"] = gate["oracle_passes"] == gate["oracle_total"] == 8
            self.beat("Execute: a ninth input uses the promoted program with zero model calls.")
            fresh = await self.run("ninth", "program", original)
            self.evidence["criteria"]["C4"] = fresh["result"]["model_calls"] == 0
            self.beat("Move the UI: HTTP invoice/payment operations survive presentation changes.")
            presentation = await self.run(
                "presentation",
                "program",
                original,
                ["reorder_fields:invoice", "move_button:mark_paid"],
            )
            self.beat("Break the request contract: resume at the failed write and propose a patch.")
            mutations = ["rename_field:invoice_total"]
            repaired = await self.run("repair", "repair", original, mutations)
            self.evidence["criteria"]["C5"] = (
                presentation["result"]["model_calls"] == 0 and repaired["result"]["model_calls"] > 0
            )
            repaired_hash = repaired["candidate_hash"]
            assert repaired_hash != original and repaired["gate_id"]
            changed = await self.run("post_repair", "program", repaired_hash, mutations)
            lost = await self.run(
                "lost_response", "repair", repaired_hash, mutations, lost="invoice-write"
            )
            self.evidence["criteria"]["C6"] = (
                changed["result"]["model_calls"] == 0 and lost["result"]["model_calls"] == 0
            )
            current = Program.model_validate_json(
                (
                    self.settings.runs_dir.parent
                    / "programs"
                    / "crm.create_invoice"
                    / repaired_hash
                    / "program.json"
                ).read_text()
            )
            negatives = []
            self.evidence["negative_gates"] = negatives
            self.beat(
                "Reject regressions: wrong values, missing/duplicate writes, and unnecessary work."
            )
            for kind in ("wrong_amount", "omitted_payment", "duplicate_write", "costlier"):
                candidate = current.model_copy(deep=True)
                candidate.version += 1
                candidate.parent_hash = repaired_hash
                candidate.notes = kind
                if kind == "wrong_amount":
                    step = next(s for s in candidate.steps if s.id == "invoice-write")
                    step.body["amount_due_cents"] = LiteralRef(value=0)
                elif kind == "omitted_payment":
                    candidate.steps = [s for s in candidate.steps if s.id != "payment-write"]
                elif kind == "duplicate_write":
                    copies = [
                        s.model_copy(deep=True)
                        for s in candidate.steps
                        if s.id in ("invoice-form", "invoice-write")
                    ]
                    for step in copies:
                        step.id = "duplicate-" + step.id
                        if step.operation_key:
                            step.operation_key = "duplicate-" + step.operation_key
                        step.depends_on = ["duplicate-" + d for d in step.depends_on]
                    position = next(
                        i for i, s in enumerate(candidate.steps) if s.id == "payment-form"
                    )
                    candidate.steps[position:position] = copies
                else:
                    candidate = with_redundant_fill(candidate)
                bad_gate, decision = await self.gate(candidate, changed["result"]["environment"])
                negatives.append(
                    {
                        "kind": kind,
                        "gate": bad_gate,
                        "decision": decision.model_dump()
                        if hasattr(decision, "model_dump")
                        else decision,
                    }
                )
                self.save()
                assert decision["verdict"] == "rejected", kind
                assert (bad_gate["status"] == "passed") == (kind == "costlier"), kind
            self.evidence["negative_gates"] = negatives
            self.evidence["criteria"]["C7"] = all(
                n["decision"]["verdict"] == "rejected" for n in negatives
            )
            # Durable events prove all beats are real server jobs and can replay in the console.
            self.evidence["criteria"]["C8"] = all(
                (self.settings.runs_dir / r["run_id"] / "events.jsonl").is_file()
                for r in self.evidence["runs"].values()
            )
            self.evidence["audit"] = audit_core(self.evidence, self.settings)
            assert all(self.evidence["criteria"].values())
            self.evidence.update(passed=True, finished=time.time(), program_hash=repaired_hash)
            self.save()
            print(
                json.dumps(
                    {"demo_id": self.id, "passed": True, "criteria": self.evidence["criteria"]}
                ),
                flush=True,
            )
        finally:
            self.save()
            await self.client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--core", action="store_true")
    modes.add_argument("--full", action="store_true")
    parser.add_argument("--assert", dest="assertions", action="store_true")
    parser.add_argument("--stage", action="store_true")
    args = parser.parse_args()
    if args.full:
        raise SystemExit(
            "Full mode remains pending E1-E6 integration; core is not full completion."
        )
    asyncio.run(Demo(args.stage).core())
