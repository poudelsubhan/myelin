"""Phase 1 integration: actual UI interpreter on a fresh ninth input via server."""

import asyncio
import json
from pathlib import Path

import httpx
from run_vision import run


async def main():
    program = json.loads(Path("tests/fixtures/crm-ui-program.json").read_text())
    async with httpx.AsyncClient() as client:
        response = await client.post("http://localhost:8100/candidates", json={"program": program})
        response.raise_for_status()
        candidate_hash = response.json()["candidate_hash"]
    result = await run(
        "program",
        candidate_hash,
        {
            "customer": "Élan Design",
            "description": 'Fresh "quoted" café',
            "quantity": 7,
            "unit_price_cents": 1001,
            "due_date": "2028-02-29",
        },
    )
    assert result["result"]["success"]
    assert result["result"]["model_calls"] == 0


if __name__ == "__main__":
    asyncio.run(main())
