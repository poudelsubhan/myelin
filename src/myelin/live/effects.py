"""Frozen logical effects are bound BEFORE action dispatch, never inferred afterward."""

import asyncio
import re
from urllib.parse import urlsplit

from myelin.live.journal import UnresolvedEffect
from myelin.program.bindings import json_path, resolve


class EffectBoundary:
    def __init__(self, journal, spec, inputs, scope_hash, verifier, emit):
        self.journal, self.spec, self.inputs = journal, spec, inputs
        self.scope, self.verifier, self.emit = scope_hash, verifier, emit
        self.task = inputs[spec.task_key_field]
        self.effects = {e.key: e for e in spec.effect_contract}
        self.action_bindings = {}
        self.active = None
        self.dispatched = False
        self.blocked = None
        self.dispatch_event = asyncio.Event()

    async def begin(self, action_id, effect_key):
        if self.blocked:
            raise UnresolvedEffect(self.blocked)
        if self.active:
            raise UnresolvedEffect("previous effect has not been reconciled")
        if effect_key is None:
            return True
        if effect_key not in self.effects:
            raise ValueError("action must declare a frozen effect key before dispatch")
        effect = self.effects[effect_key]
        self.action_bindings[action_id] = effect_key
        prepared = self.journal.prepare(
            self.scope, self.task, effect_key, self.inputs, effect.depends_on
        )
        if prepared:
            self.active, self.dispatched = effect, False
            self.dispatch_event.clear()
        return prepared

    async def authorize_request(self, session, method, url, body):
        if self.blocked or self.active is None:
            self.blocked = "unmapped live mutation blocked before dispatch"
            raise UnresolvedEffect(self.blocked)
        effect = self.active
        match, p = effect.request, urlsplit(url)
        path_match = re.fullmatch(match.path_pattern, p.path)
        if (
            method != match.method
            or f"{p.scheme}://{p.netloc}" != match.origin
            or path_match is None
        ):
            self.blocked = "request does not match the declared logical effect"
            raise UnresolvedEffect(self.blocked)
        for group, ref in match.path_bindings.items():
            if path_match.group(group) != str(
                resolve(ref, self.inputs, session.variables, session.secrets)
            ):
                self.blocked = "request path targets the wrong resource"
                raise UnresolvedEffect(self.blocked)
        for path, template in match.body_templates.items():
            expected = template.format_map(self.inputs | session.variables)
            if json_path(body, path) != expected:
                self.blocked = "request body targets the wrong resource"
                raise UnresolvedEffect(self.blocked)
        for path, ref in match.required_body.items():
            expected = resolve(ref, self.inputs, session.variables, session.secrets)
            actual = json_path(body, path) if path.startswith("$") else (body or {}).get(path)
            if actual != expected:
                self.blocked = "mutation payload does not match frozen input bindings"
                raise UnresolvedEffect(self.blocked)
            if path == effect.initial_marker_field and self.task not in str(actual):
                self.blocked = "initial create must persist the stable task reference"
                raise UnresolvedEffect(self.blocked)
        for path, refs in match.body_contains.items():
            actual = json_path(body, path)
            if not isinstance(actual, str) or not all(
                str(resolve(ref, self.inputs, session.variables, session.secrets)) in actual
                for ref in refs
            ):
                self.blocked = "mutation body is missing required exact business text"
                raise UnresolvedEffect(self.blocked)
        # Durable commit precedes route.continue_ (and therefore the actual request).
        self.journal.dispatch(
            self.scope, self.task, effect.key, {"method": method, "url": url, "body": body}
        )
        self.dispatched = True
        self.dispatch_event.set()
        await self.emit("live.effect.dispatched", {"effect_key": effect.key, "task_key": self.task})

    async def finish(self, session, error=None):
        if self.active is not None and not self.dispatched and not error and not self.blocked:
            try:
                await asyncio.wait_for(self.dispatch_event.wait(), timeout=3)
            except TimeoutError:
                pass
        effect, sent = self.active, self.dispatched
        self.active = None  # Verification and its navigation can never authorize writes.
        if effect is None:
            if self.blocked:
                raise UnresolvedEffect(self.blocked)
            return
        if not sent:
            self.journal.finish(
                self.scope,
                self.task,
                effect.key,
                "not_applied",
                {"definitely_not_applied": True, "reason": "dispatch guard not passed"},
            )
            if error or self.blocked:
                raise UnresolvedEffect(self.blocked or "action failed before dispatch")
            raise ValueError("declared effect produced no matching request")
        evidence = await self.verifier.verify(session, self.spec, self.inputs, effect.assertions)
        state = "verified" if evidence.status == "verified" else "unknown"
        self.journal.finish(
            self.scope, self.task, effect.key, state, evidence.model_dump(mode="json")
        )
        if state == "unknown" or self.blocked:
            self.blocked = self.blocked or "write outcome remains unknown; read-back required"
            await self.emit(
                "live.effect.unknown", {"effect_key": effect.key, "task_key": self.task}
            )
            raise UnresolvedEffect(self.blocked)

    async def reconcile(self, session):
        for row in self.journal.rows(self.scope, self.task):
            if row["state"] not in ("dispatched", "unknown"):
                continue
            effect = self.effects.get(row["operation_key"])
            if effect is None:
                raise UnresolvedEffect("unknown effect cannot be renamed across contracts")
            evidence = await self.verifier.verify(
                session, self.spec, self.inputs, effect.assertions
            )
            if evidence.status != "verified":
                raise UnresolvedEffect("uncertain mutation remains unresolved; no retry authorized")
            self.journal.finish(
                self.scope, self.task, effect.key, "verified", evidence.model_dump(mode="json")
            )
