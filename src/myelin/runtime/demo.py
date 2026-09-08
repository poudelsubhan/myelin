"""Preserve the owned-app behavior behind the shared interpreter seam."""

from myelin.adapters.crm import compatible
from myelin.verification.oracle import verify


class DemoRuntime:
    is_demo = True

    def __init__(self, admin, verifier=verify):
        self.admin = admin
        self.verifier = verifier

    def compatible(self, program, environment):
        return compatible(program, environment)

    def definitions_after(self, program):
        return {}

    async def observe(self, session):
        return await self.admin.state(session.tenant)

    async def verify(self, session, program, before, after, inputs):
        return self.verifier(
            program.workflow, before, after, inputs, {"revision": program.policy_revision}
        )

    async def before_step(self, session, step, inputs):
        pass

    async def after_step(self, session, step, inputs):
        pass

    async def resume(self, session, url):
        if getattr(getattr(session, "environment", None), "app", None) == "expense":
            from myelin.adapters.expense import resume

            await resume(session, url)

    def effect_status(self):
        return "unknown"

    def lift(self, trace, network):
        from myelin.program.lifter import lift

        return lift(trace, network)
