"""Handwritten Phase 5 contract fixture, explicitly not model-compilation evidence."""

import asyncio
import json
from uuid import uuid4

from myelin.adapters.expense import revision
from myelin.browser.session import Session
from myelin.config import ROOT, Settings
from myelin.console.bus import EventBus
from myelin.gate.gate import GateRunner, suite, suite_hash
from myelin.gate.ledger import CandidateStore
from myelin.program.executor import Executor
from myelin.schema import EnvironmentSpec, GateRequest, Program
from myelin.trace.store import TraceStore
from myelin.verification.admin import Admin


def fixture(mutations=()):
    def literal(value):
        return {"kind": "literal", "value": value}

    def ref(kind, key):
        return {"kind": kind, "key": key}

    exists = {
        "kind": "compare",
        "source": "input",
        "path": "merchant",
        "op": "exists",
        "expected": literal(True),
    }
    ok = {
        "kind": "compare",
        "source": "response",
        "path": "status",
        "op": "eq",
        "expected": literal(200),
    }

    def common(sid, effect="read"):
        return {
            "id": sid,
            "intent": sid,
            "source_action_ids": ["manual-fixture"],
            "pre": [exists],
            "post": [exists],
            "effect": effect,
            "operation_key": sid if effect == "write" else None,
        }

    auth = {"Authorization": ref("secret", "bearer_token")}
    login = common("expense-login", "write") | {
        "kind": "http",
        "method": "POST",
        "url": literal("/api/login"),
        "headers": {},
        "body_kind": "json",
        "body": {k: ref("secret", k) for k in ("email", "password", "tenant")},
        "extract": [
            {
                "target_var": "bearer_token",
                "source": "json_path",
                "expression": "$.token",
                "secret": True,
            }
        ],
        "post": [ok],
    }
    listing = common("expense-list") | {
        "kind": "http",
        "method": "GET",
        "url": literal("/api/expenses"),
        "headers": auth,
        "body_kind": "none",
        "post": [ok],
    }
    body = {
        k: ref("input", k) for k in ("merchant", "amount_cents", "category", "date", "receipt_text")
    }
    if "rename_field:amount" in mutations:
        body["cost_cents"] = body.pop("amount_cents")
    create = common("expense-create", "write") | {
        "kind": "http",
        "method": "POST",
        "url": literal("/api/expenses"),
        "headers": auth,
        "body_kind": "json",
        "body": body,
        "post": [ok],
        "extract": [
            {"target_var": "expense_id", "source": "json_path", "expression": "$.id"},
            {"target_var": "expense_resume", "source": "json_path", "expression": "$.resume_url"},
        ],
    }
    confirm = common("threshold-confirm") | {
        "kind": "ui",
        "action": "check",
        "target": {"strategy": "label", "value": "Confirm expense above $500"},
        "arguments": {"value": literal(True)},
        "resume_url": ref("variable", "expense_resume"),
    }
    branch = common("threshold") | {
        "kind": "branch",
        "condition": {
            "kind": "compare",
            "source": "input",
            "path": "amount_cents",
            "op": "gt",
            "expected": literal(50000),
        },
        "then": [confirm],
        "otherwise": [],
        "source_steer_id": "manual-fixture-no-live-steer",
        "rationale": "Handwritten confirmation fixture; not a learned policy.",
    }
    submit = common("expense-submit", "write") | {
        "kind": "ui",
        "action": "submit",
        "target": {"strategy": "role", "role": "button", "value": "Submit expense"},
        "arguments": {},
        "resume_url": ref("variable", "expense_resume"),
    }
    steps = [login, listing, create]
    if "add_step:confirm_submit" in mutations:
        extra = dict(confirm) | {
            "id": "server-confirm",
            "target": {"strategy": "label", "value": "Confirm submission authorization"},
        }
        steps.append(extra)
    steps += [branch, submit]
    return Program.model_validate(
        {
            "workflow": "expense.submit_expense",
            "app": "expense",
            "version": 1,
            "input_schema": {},
            "policy_revision": "expense-policy-v1",
            "supported_revisions": [revision(mutations)],
            "steps": steps,
            "final_post": [
                {
                    "kind": "compare",
                    "source": "business",
                    "path": "$.expenses[0].status",
                    "op": "eq",
                    "expected": literal("submitted"),
                }
            ],
            "compiled_from": ["manual-fixture"],
            "notes": "Handwritten adapter/branch fixture; no model compilation or live steering.",
        }
    )


async def main():
    settings = Settings.load().for_app("expense")
    admin = Admin(settings)
    bus = EventBus(settings.runs_dir)
    candidates = CandidateStore(ROOT / "programs")
    root = "phase5-expense-" + str(uuid4())
    store = TraceStore(settings.runs_dir, root)

    async def emit(kind, payload):
        return await bus.emit(root, kind, payload)

    async def execute(program, inputs, environment, run_id):
        local = TraceStore(settings.runs_dir, run_id)
        await admin.reset(run_id, environment)
        session = Session(settings, local, run_id, run_id)
        try:
            await session.open(environment, run_id)
            result = await Executor(admin, emit)(program, inputs, environment, session)
            local.save("business-after.json", await admin.state(run_id))
            return result
        finally:
            await session.close()

    program = fixture()
    (ROOT / "tests/fixtures/expense-branch-program.json").write_text(
        program.model_dump_json(indent=2)
    )
    digest = candidates.put(program)
    env = EnvironmentSpec(app="expense", revision=revision([]), seed_version="expense-seed-v1")
    request = GateRequest(
        candidate_hash=digest,
        workflow=program.workflow,
        environment=env,
        policy_revision=program.policy_revision,
        suite_hash=suite_hash(program.workflow, program.policy_revision),
        reference_mode="none",
        reference_case_ids=[],
    )
    gate = await GateRunner(settings.runs_dir / "gates", candidates, execute, emit)(request)
    print(
        json.dumps(
            {
                "run_id": root,
                "gate_id": gate.gate_id,
                "status": gate.status,
                "passes": gate.oracle_passes,
            }
        ),
        flush=True,
    )
    assert gate.status == "passed", gate.model_dump_json()
    mutations = ["rename_field:amount", "add_step:confirm_submit", "throttle:list"]
    env = env.model_copy(update={"mutations": mutations, "revision": revision(mutations)})
    mutation_run = str(uuid4())
    result = await execute(fixture(mutations), suite(program.workflow)[1].inputs, env, mutation_run)
    evidence = {
        "run_id": root,
        "gate_id": gate.gate_id,
        "candidate_hash": digest,
        "oracle_passes": gate.oracle_passes,
        "mutation_run": mutation_run,
        "mutation_success": result.success,
        "fresh_auth": True,
        "status": "passed" if result.success else "failed",
    }
    store.save("evidence.json", evidence)
    print(json.dumps(evidence), flush=True)
    assert result.success, result.model_dump_json()


if __name__ == "__main__":
    asyncio.run(main())
