import argparse
import asyncio
import json

import httpx


async def run(mode="record", program_hash=None, inputs=None):
    inputs = inputs or {
        "customer": "Acme Labs",
        "description": 'Live "invoice" café',
        "quantity": 3,
        "unit_price_cents": 1999,
        "due_date": "2026-09-30",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "http://localhost:8100/runs",
            json={
                "workflow": "crm.create_invoice",
                "inputs": inputs,
                "mode": mode,
                "program_hash": program_hash,
                "environment": {
                    "app": "crm",
                    "revision": "crm-v1",
                    "mutations": [],
                    "seed_version": "crm-seed-v1",
                },
            },
        )
        response.raise_for_status()
        run_id = response.json()["run_id"]
        print("Run:", run_id, flush=True)
        for _ in range(240):
            status = (await client.get(f"http://localhost:8100/runs/{run_id}")).json()
            if status["status"] != "running":
                print(json.dumps(status, indent=2))
                return status
            await asyncio.sleep(1)
        raise TimeoutError("orchestrator did not finish within CLI ceiling")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--inputs", help="JSON input file")
    args = p.parse_args()
    inputs = json.loads(open(args.inputs).read()) if args.inputs else None
    result = asyncio.run(run(inputs=inputs))
    raise SystemExit(0 if result["status"] == "completed" else 1)
