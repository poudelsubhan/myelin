import asyncio
import json

import pytest

from myelin.astra.async_tools import AsyncTools
from myelin.trace.store import TraceStore


async def test_out_of_order_exactly_once_original_ids_and_handles(tmp_path):
    registry = AsyncTools(TraceStore(tmp_path, "async"))
    release = asyncio.Event()

    async def slow():
        await release.wait()
        return "slow result"

    async def fast():
        return "fast result"

    registry.launch("call-slow", "model-slow", slow)
    registry.launch("call-fast", "model-fast", fast)
    with pytest.raises(ValueError, match="unique"):
        registry.launch("other-call", "model-slow", fast)
    await asyncio.sleep(0)
    outputs = registry.completed()
    assert [o["call_id"] for o in outputs] == ["call-fast"]
    assert registry.completed() == []
    with pytest.raises(ValueError):
        registry.launch("call-fast", "different", fast)
    release.set()
    outputs = await registry.wait("wait-call", ["model-slow"])
    assert [o["call_id"] for o in outputs] == ["call-slow", "wait-call"]
    assert json.loads(outputs[-1]["output"]) == {"status": {"model-slow": "completed"}}
    assert await registry.close() == []


async def test_failure_and_orphan_shutdown(tmp_path):
    registry = AsyncTools(TraceStore(tmp_path, "async-failed"))

    async def failure():
        raise RuntimeError("deliberate")

    registry.launch("failed", "model-failed", failure)
    registry.launch("cancelled", "model-cancelled", lambda: asyncio.sleep(3600))
    await asyncio.sleep(0)
    outputs = await registry.close()
    assert len(outputs) == 2
    assert all(json.loads(o["output"])["status"] == "failed" for o in outputs)
    assert await registry.close() == []
