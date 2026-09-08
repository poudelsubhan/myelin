"""Live expense compilation, steering, branch validation and ninth-input execution."""

import asyncio
import json
from uuid import uuid4

import httpx

from myelin.config import Settings
from myelin.steer.policy import TEXT


async def main():
    settings = Settings.load()
    root = settings.runs_dir / ("phase6-expense-" + str(uuid4()))
    root.mkdir(parents=True)
    evidence = {"run_id": root.name, "runs": {}}
    env = {"app": "expense", "revision": "expense-v1", "seed_version": "expense-seed-v1"}
    inputs = {
        "merchant": "Summit Travel",
        "amount_cents": 65000,
        "category": "Travel",
        "date": "2026-09-08",
        "receipt_text": 'Train receipt "A"',
    }
    async with httpx.AsyncClient(base_url="http://localhost:8100", timeout=30) as client:
        reset = await client.post(
            "/demo/reset-ledger",
            headers={"X-Myelin-Demo-Token": settings.demo_token},
            json={"workflow": "expense.submit_expense"},
        )
        reset.raise_for_status()

        async def run(label, mode, policy="expense-policy-v1", digest=None, steer=None, data=None):
            response = await client.post(
                "/runs",
                json={
                    "workflow": "expense.submit_expense",
                    "mode": mode,
                    "inputs": data or inputs,
                    "environment": env,
                    "policy_revision": policy,
                    "program_hash": digest,
                    "steer_text": steer,
                },
            )
            response.raise_for_status()
            rid = response.json()["run_id"]
            print(json.dumps({"phase6": root.name, "label": label, "run_id": rid}), flush=True)
            async with asyncio.timeout(1200):
                while True:
                    status = (await client.get("/runs/" + rid)).json()
                    if status["status"] != "running":
                        break
                    await asyncio.sleep(1)
            evidence["runs"][label] = {"run_id": rid, **status}
            (root / "evidence.json").write_text(json.dumps(evidence, indent=2))
            assert status["status"] == "completed", (label, status)
            return status

        baseline = await run("baseline", "record")
        steered = await run(
            "steered", "full", steer=TEXT, data=inputs | {"merchant": "Steered Summit"}
        )
        ninth = await run(
            "ninth",
            "program",
            policy="expense-policy-manager-v2",
            digest=steered["candidate_hash"],
            data=inputs | {"merchant": "Ninth 'quoted' merchant", "amount_cents": 50000},
        )
        assert ninth["result"]["model_calls"] == 0
        gate = (await client.get("/gates/" + steered["gate_id"])).json()
        assert gate["status"] == "passed" and gate["reference_passes"] == 8
        evidence.update(
            passed=True,
            baseline_hash=baseline["candidate_hash"],
            steered_hash=steered["candidate_hash"],
            gate_id=steered["gate_id"],
        )
        (root / "evidence.json").write_text(json.dumps(evidence, indent=2))
        print(json.dumps({k: v for k, v in evidence.items() if k != "runs"}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
