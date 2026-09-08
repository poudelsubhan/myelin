"""Launch the server-owned native async/full differential proof."""

import asyncio
import json

import httpx

from myelin.gate.gate import suite


async def main():
    async with httpx.AsyncClient(base_url="http://localhost:8100", timeout=30) as client:
        current = (await client.get("/ledger/crm.create_invoice")).json()["current"]
        if not current:
            raise ValueError("Run the core demo first to create a verified CRM candidate")
        gate = (await client.get("/gates/" + current["gate_id"])).json()
        request = gate["request"] | {
            "reference_mode": "full",
            "reference_case_ids": [c.case_id for c in suite()],
        }
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
        return data["evidence"]


if __name__ == "__main__":
    asyncio.run(main())
