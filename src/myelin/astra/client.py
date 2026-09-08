import asyncio
import json
import os
from decimal import Decimal

from openai import APIConnectionError, APIStatusError, AsyncOpenAI

from myelin.config import ROOT
from myelin.contracts import FeatureUnavailable
from myelin.schema import UsageRecord


class BudgetExceeded(RuntimeError):
    pass


class Astra:
    def __init__(self, settings, store, emit):
        self.settings, self.store, self.emit = settings, store, emit
        self.usage = {}
        self.effective_efforts = {}
        previous = store.folder / "usage.jsonl"
        if previous.exists():
            for line in previous.read_text().splitlines():
                row = UsageRecord.model_validate_json(line)
                self.usage[row.response_id] = row
        self.limit = Decimal(os.getenv("MYELIN_MAX_MODEL_USD_PER_RUN", "10"))
        self.pricing = json.loads((ROOT / "policy/pricing.json").read_text())

    @property
    def usd(self):
        if any(u.usd is None for u in self.usage.values()):
            return None
        return sum((Decimal(u.usd) for u in self.usage.values()), Decimal(0))

    def effort_update(self, effort, message):
        if effort not in ("low", "medium", "high", "xhigh", "max", "ultra"):
            raise ValueError("invalid effort")
        return [
            {"type": "configuration_update", "reasoning": {"effort": effort}},
            {"role": "user", "content": message},
        ]

    async def respond(self, purpose, **kwargs):
        request_effort = kwargs.get("reasoning", {}).get("effort", "medium")
        effective = self.effective_efforts.get(kwargs.get("previous_response_id"), request_effort)
        updates = []
        pending = kwargs.get("input", [])
        if isinstance(pending, list):
            for i, item in enumerate(pending):
                if item.get("type") == "configuration_update":
                    if (
                        i + 1 >= len(pending)
                        or pending[i + 1].get("role") != "user"
                        or kwargs.get("context_management")
                        or kwargs.get("truncation") == "auto"
                    ):
                        raise ValueError(
                            "effort update needs a user item and no automatic compaction/truncation"
                        )
                    effective = item["reasoning"]["effort"]
                    updates.append(item)

        self.check_budget()
        async with AsyncOpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout_s,
            max_retries=0,
        ) as client:
            for attempt in range(3):
                try:
                    response = await client.responses.create(
                        model=self.settings.model, max_output_tokens=8192, **kwargs
                    )
                    break
                except (APIStatusError, APIConnectionError) as exc:
                    status = getattr(exc, "status_code", None)
                    retryable = status is None or status == 429 or status >= 500
                    self.store.append(
                        "api-attempts.jsonl",
                        {
                            "purpose": purpose,
                            "attempt": attempt + 1,
                            "error": type(exc).__name__,
                            "status": status,
                            "request_id": getattr(exc, "request_id", None),
                            "usage": "unknown",
                            "retrying": retryable and attempt < 2,
                        },
                    )
                    if not retryable or attempt == 2:
                        raise
                    await asyncio.sleep(2**attempt)
        self.effective_efforts[response.id] = effective
        self.store.append(
            "effort.jsonl",
            {
                "response_id": response.id,
                "previous_response_id": kwargs.get("previous_response_id"),
                "request_effort": request_effort,
                "effective_effort": effective,
                "updates": updates,
            },
        )
        return await self.account(response, purpose)

    def check_budget(self):
        if not self.settings.live or not self.settings.api_key:
            raise FeatureUnavailable("Set OPENAI_API_KEY and MYELIN_LIVE=1 for live calls")
        if self.usd is None or self.usd >= self.limit:
            raise BudgetExceeded("model dollar ceiling reached or pricing unavailable")
        if len(self.usage) >= int(os.getenv("MYELIN_MAX_MODEL_TURNS", "20")):
            raise BudgetExceeded("model turn ceiling reached")

    async def account(self, response, purpose):
        if response.id not in self.usage:
            u = response.usage
            usd = None
            cached = getattr(u.input_tokens_details, "cached_tokens", 0) if u else 0
            written = getattr(u.input_tokens_details, "cache_write_tokens", None) if u else None
            if u and response.model.startswith(self.pricing["model"]) and written is not None:
                rates = self.pricing["usd_per_million"]
                long = u.input_tokens > self.pricing["long_context_threshold"]
                amounts = {
                    "input": u.input_tokens - cached - written,
                    "cached_input": cached,
                    "cache_write": written,
                    "output": u.output_tokens,
                }
                if min(amounts.values()) >= 0:
                    usd = (
                        sum(
                            Decimal(str(rates[k]))
                            * n
                            * Decimal(
                                str(self.pricing["long_context_multipliers"][k] if long else 1)
                            )
                            for k, n in amounts.items()
                        )
                        / 1_000_000
                    )
            record = UsageRecord(
                response_id=response.id,
                purpose=purpose,
                model=response.model,
                input_tokens=u.input_tokens if u else 0,
                cached_tokens=cached,
                cache_write_tokens=written,
                output_tokens=u.output_tokens if u else 0,
                model_calls=1,
                usd=str(usd) if usd is not None else None,
                pricing_revision=self.pricing["revision"] if usd is not None else None,
            )
            self.usage[response.id] = record
            self.store.append("usage.jsonl", record)
            await self.emit("cost.updated", record.model_dump(mode="json"))
        return response
