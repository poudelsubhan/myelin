"""Exercise real hosted analysis and staged patch through the orchestrator."""

import asyncio
import json

import httpx


async def main():
    async with httpx.AsyncClient(base_url="http://localhost:8100", timeout=30) as client:
        current = (await client.get("/ledger/crm.create_invoice")).json()["current"]
        response = await client.post(
            "/compiler/hosted",
            json={
                "workflow": "crm.create_invoice",
                "candidate_hash": current["candidate_hash"],
                "full_validation": True,
            },
        )
        response.raise_for_status()
        run_id = response.json()["run_id"]
        print(
            json.dumps({"hosted_run": run_id, "source_hash": current["candidate_hash"]}), flush=True
        )
        async with asyncio.timeout(1200):
            while True:
                status = (await client.get("/runs/" + run_id)).json()
                if status["status"] != "running":
                    break
                await asyncio.sleep(1)
        print(json.dumps(status), flush=True)
        assert status["status"] == "completed", status
        return status["evidence"]


if __name__ == "__main__":
    asyncio.run(main())
