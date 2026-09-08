"""Live hooks never import demo Admin, reset, reference replay or private oracles."""

from myelin.live.browser_reads import read
from myelin.live.schema import digest
from myelin.schema import AssertionResult


class LiveRuntime:
    is_demo = False

    def __init__(self, spec, site, binding, effects, verifier):
        self.spec, self.site, self.binding = spec, site, binding
        self.effects, self.verifier = effects, verifier
        self.evidence = None
        self.restoring = False

    async def restore(self, session, program, inputs):
        verified = {
            r["operation_key"]
            for r in self.effects.journal.rows(self.effects.scope, self.effects.task)
            if r["state"] == "verified"
        }
        if not verified:
            return 0, []
        for effect in self.spec.effect_contract:
            if effect.key in verified:
                evidence = await self.verifier.verify(session, self.spec, inputs, effect.assertions)
                if evidence.status != "verified":
                    raise ValueError("completed effect no longer verifies")
        indexes = [i for i, s in enumerate(program.steps) if s.operation_key in verified]
        end = max(indexes) + 1
        if {s.operation_key for s in program.steps[:end] if s.effect == "write"} != verified:
            raise ValueError("resume requires a fully verified effect prefix")
        urls = [
            session.variables[r.output_variable]
            for r in self.spec.evidence_recipes
            if r.id == "resource_urls" and r.output_variable in session.variables
        ]
        if len(urls) != 1:
            raise ValueError("resume requires one independently recovered resource URL")
        await session.perform("resume-resource", "navigate", None, {"url": urls[0]})
        self.restoring = True
        return end, [s.id for s in program.steps[:end]]

    def compatible(self, program, environment):
        b = self.binding
        return (
            b.candidate_hash == program.content_hash()
            and b.spec_hash == digest(self.spec)
            and b.site_profile_hash == digest(self.site)
            and b.account_id == self.spec.scope["account_id"]
            and b.workspace_id == self.spec.scope["workspace_id"]
            and b.resource_scope == self.spec.scope["resource_id"]
            and b.auth_profile_id == self.site.auth_profile_id
            and b.evidence_recipe_hash == digest(self.spec.evidence_recipes)
            and environment.seed_version == "not-applicable"
        )

    def definitions_after(self, program):
        return {
            step_id: [r.variable for r in reads]
            for step_id, reads in self.binding.after_step_reads.items()
        }

    def secret_definitions_before(self, program):
        return {key: list(e.cookie_bindings) for key, e in self.binding.http_evidence.items()}

    async def observe(self, session):
        return {}  # Business values come only from independently declared evidence reads.

    async def verify(self, session, program, before, after, inputs):
        self.evidence = await self.verifier.verify(session, self.spec, inputs)
        assertions = list(self.evidence.assertions)
        assertions.append(
            AssertionResult(
                name="live_readback_complete",
                passed=self.evidence.status == "verified",
                expected="verified",
                observed=self.evidence.status,
            )
        )
        return assertions

    async def _reads(self, session, mapping, step, inputs):
        recipes = {r.id: r for r in self.spec.evidence_recipes}
        for binding in mapping.get(step.id, []):
            result = await read(session, recipes[binding.recipe_id], inputs)
            if not result["complete"]:
                raise ValueError("runtime read binding is inconclusive")
            recipe = recipes[binding.recipe_id]
            value = result["value"]
            if recipe.output_index is not None:
                if not isinstance(value, list) or len(value) != 1:
                    raise ValueError("read binding requires a unique resource")
                value = value[recipe.output_index]
            session.variables[binding.variable] = value

    async def before_step(self, session, step, inputs):
        if step.id in self.binding.http_evidence:
            from myelin.live.http_recipes import prepare

            await prepare(session, step, self.binding.http_evidence[step.id], inputs)
        if self.restoring and getattr(step, "action", None) == "fill":
            # Collapsed editors commonly expose an opener with the same accessible
            # name as the recorded textbox. The write guard remains active here.
            target = step.target
            if target and target.strategy == "role" and target.role == "textbox":
                field = session.locator(target)
                opener = target.model_copy(update={"role": "button"})
                button = session.locator(opener)
                await field.or_(button).first.wait_for(state="visible")
                if not await field.count() or not await field.is_visible():
                    if await button.count() == 1 and await button.is_visible():
                        await session.perform(step.id + ":open-editor", "click", opener, {})
            self.restoring = False
        await self._reads(session, self.binding.before_step_reads, step, inputs)
        key = self.binding.effect_bindings.get(step.id)
        if (step.effect == "write") != (key is not None) or (key and key != step.operation_key):
            raise ValueError("compiled action lost its frozen effect mapping")
        session.next_effect_key = key

    async def after_step(self, session, step, inputs):
        await self._reads(session, self.binding.after_step_reads, step, inputs)

    async def resume(self, session, url):
        session.origin_policy.navigation(url)

    def effect_status(self):
        rows = self.effects.journal.rows(self.effects.scope, self.effects.task)
        if any(r["state"] in ("unknown", "dispatched") for r in rows):
            return "unknown"
        return "not_applied"

    def lift(self, trace, network):
        return []  # UI compilation is first-class; HTTP needs observed provider evidence.
