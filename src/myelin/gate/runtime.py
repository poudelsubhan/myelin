"""Shared fresh-session callbacks for the console and standalone live gates."""

import json

from myelin.browser.session import Session
from myelin.program.executor import Executor
from myelin.trace.store import TraceStore
from myelin.verification import expense, oracle
from myelin.verification.admin import Admin


class GateRuntime:
    def __init__(self, settings, bus):
        self.settings, self.bus = settings, bus

    async def program(self, program, inputs, environment, run_id):
        settings = self.settings.for_app(environment.app)
        admin = Admin(settings)
        store = TraceStore(settings.runs_dir, run_id)

        async def emit(kind, payload):
            return await self.bus.emit(run_id, kind, store.sanitizer.clean(payload))

        await admin.reset(run_id, environment)
        session = Session(settings, store, run_id, run_id)
        try:
            await session.open(environment, run_id)
            await emit("run.started", {"mode": "program", "gate_case": True})
            result = await Executor(admin, emit)(program, inputs, environment, session)
            store.save("business-after.json", await admin.state(run_id))
            await emit(
                "run.finished", {"status": result.status, "result": result.model_dump(mode="json")}
            )
            return result
        finally:
            await session.close()

    async def outcome(self, program, inputs, run_id):
        state = json.loads((self.settings.runs_dir / run_id / "business-after.json").read_text())
        return (expense if program.app == "expense" else oracle).projection(state, inputs)

    async def reference(self, program, inputs, environment, run_id):
        from myelin.gate.reference import run_reference

        return await run_reference(
            self.settings.for_app(environment.app), program, inputs, environment, run_id, self.bus
        )
