"""Required business assertions live outside the compiler's write surface."""

import asyncio
import time
from datetime import datetime

from myelin.live.browser_reads import read
from myelin.live.schema import EvidenceResult
from myelin.program.bindings import resolve
from myelin.schema import AssertionResult


def compare(operator, actual, expected):
    if operator == "equals":
        return type(actual) is type(expected) and actual == expected
    if operator == "contains":
        return isinstance(actual, (str, list, dict)) and expected in actual
    if operator in ("count_equals", "unique"):
        return isinstance(actual, list) and len(actual) == (1 if operator == "unique" else expected)
    if operator == "date_equals":
        try:
            a = datetime.fromisoformat(actual.replace("Z", "+00:00"))
            e = datetime.fromisoformat(expected.replace("Z", "+00:00"))
            return a.tzinfo is not None and e.tzinfo is not None and a == e
        except (ValueError, TypeError, AttributeError):
            return False
    return False


class Verifier:
    def __init__(self, reader=read, attempts=4, delay=0.75):
        self.reader, self.attempts, self.delay = reader, attempts, delay

    async def identity(self, session, site, spec, inputs):
        observed = {}
        for key, recipe in site.scope_locator.items():
            result = await self.reader(session, recipe, inputs)
            if not result["complete"] or result["value"] != spec.scope[key]:
                raise ValueError("needs_auth or wrong account/workspace/resource")
            observed[key] = result["value"]
        return observed

    async def verify(self, session, spec, inputs, names=None):
        assertions = [a for a in spec.outcome_contract if names is None or a.name in names]
        recipes = {r.id: r for r in spec.evidence_recipes}
        for attempt in range(self.attempts):
            session.evidence_cache = {}  # Never reuse evidence collected before the current write.
            results, limitations, urls, ids = [], [], [], []
            try:
                identity = await self.identity(session, session.site, spec, inputs)
            except Exception:
                return EvidenceResult(
                    status="inconclusive",
                    assertions=[],
                    captured_at=time.time(),
                    scope_identity={},
                    limitations=["scope identity unavailable"],
                )
            cache = {}
            for assertion in assertions:
                recipe = recipes[assertion.recipe_id]
                expected = resolve(assertion.expected, inputs, session.variables, session.secrets)
                try:
                    if recipe.id not in cache:
                        cache[recipe.id] = await self.reader(session, recipe, inputs)
                    result = cache[recipe.id]
                except Exception:
                    result = {"value": None, "complete": False}
                actual = result["value"]
                passed = result["complete"] and compare(assertion.operator, actual, expected)
                if not result["complete"] and assertion.required:
                    limitations.append(f"{assertion.name}: incomplete read-back")
                results.append(
                    AssertionResult(
                        name=assertion.name, passed=passed, expected=expected, observed=actual
                    )
                )
                if passed and recipe.output_variable:
                    output = actual
                    if recipe.output_index is not None:
                        if not isinstance(actual, list) or len(actual) != 1:
                            raise ValueError(
                                "resource bindings require exactly one read-back match"
                            )
                        output = actual[recipe.output_index]
                    session.variables[recipe.output_variable] = output
                if passed and recipe.id == "resource_urls":
                    urls.extend(actual if isinstance(actual, list) else [actual])
                if passed and recipe.id == "resource_ids":
                    ids.extend(actual if isinstance(actual, list) else [actual])
            required = {a.name for a in assertions if a.required}
            passed = all(a.passed for a in results if a.name in required)
            status = "inconclusive" if limitations else "verified" if passed else "failed"
            evidence = EvidenceResult(
                status=status,
                assertions=results,
                resource_ids=ids,
                resource_urls=urls,
                captured_at=time.time(),
                scope_identity=identity,
                limitations=limitations,
            )
            if status == "verified" or attempt + 1 == self.attempts:
                return evidence
            await asyncio.sleep(self.delay)
