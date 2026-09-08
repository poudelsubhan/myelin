import json
import os
from decimal import Decimal

from openai import AsyncOpenAI

from myelin.config import ROOT
from myelin.contracts import FeatureUnavailable
from myelin.schema import UsageRecord


class BudgetExceeded(RuntimeError):
    pass


class Astra:
    def __init__(self, settings, store, emit):
        self.settings, self.store, self.emit = settings, store, emit
        self.usage = {}
        self.limit = Decimal(os.getenv("MYELIN_MAX_MODEL_USD_PER_RUN", "10"))
        self.pricing = json.loads((ROOT / "policy/pricing.json").read_text())

    @property
    def usd(self):
        if any(u.usd is None for u in self.usage.values()):
            return None
        return sum((Decimal(u.usd) for u in self.usage.values()), Decimal(0))

    async def respond(self, purpose, **kwargs):
        if not self.settings.live or not self.settings.api_key:
            raise FeatureUnavailable("Set OPENAI_API_KEY and MYELIN_LIVE=1 for live calls")
        if self.usd is None or self.usd >= self.limit:
            raise BudgetExceeded("model dollar ceiling reached or pricing unavailable")
        if len(self.usage) >= int(os.getenv("MYELIN_MAX_MODEL_TURNS", "20")):
            raise BudgetExceeded("model turn ceiling reached")
        async with AsyncOpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout_s,
            max_retries=0,
        ) as client:
            response = await client.responses.create(
                model=self.settings.model, max_output_tokens=8192, **kwargs
            )
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
