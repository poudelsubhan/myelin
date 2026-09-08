import asyncio
import json

import httpx

from myelin.adapters.crm import revision
from myelin.config import Settings


async def submit(mode, digest, mutations, *, lost=None):
    headers = {"X-Myelin-Demo-Token": Settings.load().demo_token} if lost else {}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "http://localhost:8100/runs",
            headers=headers,
            json={
                "workflow": "crm.create_invoice",
                "inputs": {
                    "customer": "Birch Studio",
                    "description": "Repair 'quoted' invoice",
                    "quantity": 2,
                    "unit_price_cents": 3177,
                    "due_date": "2027-01-31",
                },
                "mode": mode,
                "program_hash": digest,
                "environment": {
                    "app": "crm",
                    "revision": revision(mutations),
                    "mutations": mutations,
                    "seed_version": "crm-seed-v1",
                },
                "lost_response_step": lost,
            },
        )
        response.raise_for_status()
        run_id = response.json()["run_id"]
        print("run", run_id, flush=True)
        for _ in range(360):
            status = (await client.get(f"http://localhost:8100/runs/{run_id}")).json()
            if status["status"] != "running":
                print(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "status": status["status"],
                            "error": status.get("error"),
                            "candidate_hash": status.get("candidate_hash"),
                            "gate_id": status.get("gate_id"),
                            "success": (status.get("result") or {}).get("success"),
                        },
                        indent=2,
                    )
                )
                return status
            await asyncio.sleep(1)
        raise TimeoutError("repair integration timeout")


async def main():
    digest = "6f83a7afde1d4e02b2e67826242f22557bcc9d986cafb420520724310fd04187"
    display = await submit("program", digest, ["reorder_fields:invoice", "move_button:mark_paid"])
    assert display["result"]["success"] and display["result"]["model_calls"] == 0
    repaired = await submit("repair", digest, ["rename_field:invoice_total"])
    assert repaired["status"] == "completed" and repaired["gate_id"]
    fresh = await submit("program", repaired["candidate_hash"], ["rename_field:invoice_total"])
    assert fresh["result"]["success"] and fresh["result"]["model_calls"] == 0
    lost = await submit(
        "repair", repaired["candidate_hash"], ["rename_field:invoice_total"], lost="invoice-write"
    )
    assert lost["result"]["success"] and lost["result"]["model_calls"] == 0


if __name__ == "__main__":
    asyncio.run(main())
