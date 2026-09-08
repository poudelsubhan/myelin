"""Handwritten integration from actual lifter evidence, before model compilation."""

import asyncio
import json
from pathlib import Path

import httpx

from myelin.gate.gate import suite_hash
from myelin.program.lifter import lift
from myelin.schema import NetworkEvent, Program, Trace


def evidence_program(run_id):
    folder = Path("runs") / run_id
    trace = Trace.model_validate_json((folder / "trace.json").read_text())
    network = [
        NetworkEvent.model_validate_json(line)
        for line in (folder / "network.jsonl").read_text().splitlines()
    ]
    candidates = lift(trace, trace.inputs, network)
    assert len(candidates) == 4, [(c.id, c.unresolved) for c in candidates]
    assert all(c.confidence == "high" for c in candidates)
    fixture = Program.model_validate_json(Path("tests/fixtures/crm-ui-program.json").read_text())
    prefix = fixture.steps[:6]
    for step in prefix:
        matches = [
            a
            for a in trace.actions
            if a.operation == step.action
            and (
                a.target == step.target
                if step.target
                else a.arguments.get("url") == step.arguments.get("url")
            )
        ]
        # Role equality can differ only in optional exact field defaults.
        if not matches and step.target:
            matches = [
                a
                for a in trace.actions
                if a.target and a.target.value == step.target.value and a.operation == step.action
            ]
        assert matches, step.id
        step.source_action_ids = [matches[-1].id]
    program = fixture.model_copy(
        update={
            "steps": prefix + [c.step for c in candidates],
            "compiled_from": [trace.run_id],
            "notes": "Handwritten from real lifted evidence; Phase 2",
        }
    )
    (folder / "http-candidates.json").write_text(
        json.dumps([c.model_dump(mode="json") for c in candidates], indent=2)
    )
    return program


async def check(program, promote=False):
    async with httpx.AsyncClient(timeout=240) as client:
        response = await client.post(
            "http://localhost:8100/candidates", json={"program": program.model_dump(mode="json")}
        )
        response.raise_for_status()
        digest = response.json()["candidate_hash"]
        response = await client.post(
            "http://localhost:8100/gates",
            json={
                "request": {
                    "candidate_hash": digest,
                    "workflow": program.workflow,
                    "environment": {
                        "app": "crm",
                        "revision": "crm-v1",
                        "mutations": [],
                        "seed_version": "crm-seed-v1",
                    },
                    "policy_revision": program.policy_revision,
                    "suite_hash": suite_hash(),
                    "reference_mode": "none",
                    "reference_case_ids": [],
                }
            },
        )
        response.raise_for_status()
        gate_id = response.json()["gate_id"]
        print("gate", gate_id, "candidate", digest, flush=True)
        for _ in range(240):
            result = (await client.get(f"http://localhost:8100/gates/{gate_id}")).json()
            if result["status"] != "pending":
                break
            await asyncio.sleep(1)
        print(
            {
                k: result.get(k)
                for k in (
                    "status",
                    "oracle_passes",
                    "oracle_total",
                    "reference_total",
                    "max_work_units",
                    "error",
                )
            },
            flush=True,
        )
        if result["status"] != "passed":
            print(
                [
                    (c["case_id"], [a for a in c["assertions"] if not a["passed"]])
                    for c in result.get("cases", [])
                ]
            )
        if promote:
            decision = await client.post(
                "http://localhost:8100/promotions",
                json={
                    "gate_id": gate_id,
                    "expected_parent_hash": program.parent_hash,
                    "mode": "initial" if program.parent_hash is None else "optimization",
                },
            )
            decision.raise_for_status()
            print("promotion", decision.json()["verdict"], decision.json()["reason"], flush=True)
        return result, digest


async def main():
    program = evidence_program("65150b6a-d1cc-4d0b-a53f-8497d69ba410")
    result, digest = await check(program, True)
    assert result["status"] == "passed"
    bad = program.model_copy(deep=True)
    bad.parent_hash = digest
    bad.version = 2
    bad.steps = [s for s in bad.steps if s.id != "payment-write"]
    result, _ = await check(bad, True)
    assert result["status"] == "failed"


if __name__ == "__main__":
    asyncio.run(main())
