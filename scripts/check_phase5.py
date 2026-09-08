"""Launch the server-owned native async/full differential proof."""

import asyncio
import json

import httpx

from myelin.gate.gate import suite, suite_hash


async def main():
    request = {
        "candidate_hash": "6f83a7afde1d4e02b2e67826242f22557bcc9d986cafb420520724310fd04187",
        "workflow": "crm.create_invoice",
        "environment": {"app": "crm", "revision": "crm-v1", "seed_version": "crm-seed-v1"},
        "policy_revision": "crm-policy-v1",
        "suite_hash": suite_hash(),
        "reference_mode": "full",
        "reference_case_ids": [c.case_id for c in suite()],
    }
    async with httpx.AsyncClient(base_url="http://localhost:8100", timeout=30) as client:
        response = await client.post("/native-gates", json={"request": request})
        response.raise_for_status()
        run_id = response.json()["run_id"]
        print(json.dumps({"run_id": run_id, "owner": "orchestrator"}), flush=True)
        async with asyncio.timeout(1200):
            while True:
                data = (await client.get("/runs/" + run_id)).json()
                if data["status"] != "running":
                    break
                await asyncio.sleep(1)
        print(json.dumps(data), flush=True)
        assert data["status"] == "completed", data


if __name__ == "__main__":
    asyncio.run(main())
