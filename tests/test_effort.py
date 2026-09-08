from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from openai import InternalServerError

from myelin.astra.client import Astra
from myelin.config import Settings
from myelin.trace.store import TraceStore


async def test_configuration_update_prefix_effective_effort_and_transient_attempt(
    monkeypatch, tmp_path
):
    requests = []
    responses = 0

    class Client:
        def __init__(self, **kwargs):
            self.responses = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def create(self, **kwargs):
            nonlocal responses
            requests.append(kwargs)
            if len(requests) == 1:
                raise InternalServerError(
                    "temporary",
                    response=httpx.Response(500, request=httpx.Request("POST", "http://test/")),
                    body=None,
                )
            responses += 1
            return SimpleNamespace(
                id=f"resp-{responses}",
                model="gpt-6-astra",
                usage=SimpleNamespace(
                    input_tokens=100,
                    output_tokens=10,
                    input_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
                ),
            )

    async def no_sleep(seconds):
        pass

    async def emit(*args):
        pass

    monkeypatch.setattr("myelin.astra.client.AsyncOpenAI", Client)
    monkeypatch.setattr("myelin.astra.client.asyncio.sleep", no_sleep)
    store = TraceStore(tmp_path, "effort")
    astra = Astra(replace(Settings.load(), live=True, api_key="synthetic"), store, emit)
    first = await astra.respond("compile", reasoning={"effort": "medium"}, input="Start")
    second = await astra.respond(
        "compile",
        previous_response_id=first.id,
        reasoning={"effort": "medium"},
        input=astra.effort_update("high", "Analyze harder"),
    )
    assert all(r["reasoning"] == {"effort": "medium"} for r in requests)
    assert astra.effective_efforts[second.id] == "high"
    assert len(astra.usage) == 2
    assert len((store.folder / "api-attempts.jsonl").read_text().splitlines()) == 1
    with pytest.raises(ValueError, match="update"):
        await astra.respond(
            "compile", input=astra.effort_update("high", "unsafe prefix"), truncation="auto"
        )
