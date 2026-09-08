import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from myelin.contracts import ExecutionContext, RawObservation
from myelin.program.bindings import BindingError
from myelin.program.dataflow import validate_dataflow
from myelin.program.executor import Executor
from myelin.program.predicates import evaluate
from myelin.schema import Branch, Compare, EnvironmentSpec, HttpStep, LiteralRef, NamedRef, Program
from myelin.trace.store import TraceStore


def branch_program():
    p = Program.model_validate_json(Path("tests/fixtures/crm-ui-program.json").read_text())
    ui = p.steps[0].model_copy(deep=True)
    ui.id = "true-child"
    ui.operation_key = None
    ui.effect = "read"
    ui.arguments = {"url": LiteralRef(value="http://localhost:8101/true")}
    ui.pre = ui.post = [
        Compare(source="input", path="amount_cents", op="exists", expected=LiteralRef(value=True))
    ]
    branch = Branch(
        id="threshold",
        intent="Explicit amount policy",
        source_action_ids=["fixture"],
        pre=ui.pre,
        post=ui.post,
        effect="read",
        condition=Compare(
            source="input", path="amount_cents", op="gt", expected=LiteralRef(value=50000)
        ),
        then=[ui],
        otherwise=[],
        rationale="Explicit user instruction",
        source_steer_id="fixture-steer",
    )
    suffix = ui.model_copy(deep=True)
    suffix.id = "single-common-suffix"
    suffix.arguments = {"url": LiteralRef(value="http://localhost:8101/suffix")}
    p.steps = [branch, suffix]
    p.final_post = ui.post
    return p


@pytest.mark.parametrize("amount,expected", [(49999, False), (50000, False), (50001, True)])
async def test_runtime_selected_path_once_common_suffix_and_boundary(
    tmp_path, monkeypatch, amount, expected
):
    program = branch_program()
    store = TraceStore(tmp_path, f"branch-{amount}")
    env = EnvironmentSpec(app="crm", revision="crm-v1")
    calls, events = [], []

    class Session:
        variables = {}
        secrets = {}
        run_id = f"branch-{amount}"
        tenant = "fixture"
        http_requests = 0
        ui_actions = 0
        settings = SimpleNamespace(crm_url="http://localhost:8101")
        page = SimpleNamespace(url="about:blank")

        async def snapshot(self):
            return RawObservation(self.page.url, b"fixture", "heading: Fixture")

        async def perform(self, sid, action, target, args, op=None):
            calls.append(sid)
            self.page.url = args["url"]
            self.ui_actions += 1

    class Admin:
        async def state(self, tenant):
            return {}

    session = Session()
    session.store = store

    async def emit(kind, payload):
        events.append((kind, payload))

    monkeypatch.setattr("myelin.program.executor.verify", lambda *a: [])
    result = await Executor(Admin(), emit)(program, {"amount_cents": amount}, env, session)
    assert result.success and result.model_calls == 0
    assert calls == (["true-child"] if expected else []) + ["single-common-suffix"]
    assert json.loads((store.folder / "branches.jsonl").read_text())["decision"] is expected


def test_unavailable_branch_definition_and_strict_operand_types():
    p = branch_program()
    step = HttpStep(
        id="conditional-extract",
        intent="Read variable only in true branch",
        source_action_ids=["fixture"],
        pre=p.steps[0].pre,
        post=p.steps[0].post,
        effect="read",
        method="GET",
        url=LiteralRef(value="http://localhost:8101/read"),
        body_kind="none",
        extract=[{"target_var": "conditional", "source": "json_path", "expression": "$.id"}],
    )
    p.steps[0].then = [step]
    p.steps[-1].arguments = {"url": NamedRef(kind="variable", key="conditional")}
    p = Program.model_validate(p.model_dump())
    with pytest.raises(BindingError, match="conditional"):
        validate_dataflow(p, {"amount_cents"})
    ctx = ExecutionContext(None, {"amount_cents": "50001"}, None, "", variables={}, secret_store={})
    with pytest.raises(BindingError, match="matching"):
        evaluate(p.steps[0].condition, ctx, {})


def test_nested_branch_ids_and_dataflow():
    p = branch_program()
    nested = p.steps[0].model_copy(deep=True)
    nested.id = "nested"
    nested.then[0].id = "nested-child"
    p.steps[0].then = [nested]
    assert validate_dataflow(p, {"amount_cents"}) == p
    nested.then[0].id = "single-common-suffix"
    with pytest.raises(ValueError, match="duplicate step"):
        Program.model_validate(p.model_dump())


async def test_explicit_response_bound_navigation_resumes_http_once(tmp_path, monkeypatch):
    program = branch_program()
    ui = program.steps[-1]
    ui.arguments = {"url": NamedRef(kind="variable", key="location")}
    http = HttpStep(
        id="read",
        intent="Get fresh entity location",
        source_action_ids=["fixture"],
        pre=ui.pre,
        post=ui.post,
        effect="read",
        method="GET",
        url=LiteralRef(value="/read"),
        body_kind="none",
        extract=[{"target_var": "location", "source": "header", "expression": "location"}],
    )
    program.steps = [http, ui]
    calls = []

    class Session:
        variables = {}
        secrets = {}
        run_id = "navigate-resume"
        tenant = "fixture"
        http_requests = 1
        ui_actions = 0
        settings = SimpleNamespace(crm_url="http://localhost:8101")
        page = SimpleNamespace(url="about:blank")
        store = TraceStore(tmp_path, "navigate-resume")

        async def snapshot(self):
            return RawObservation(self.page.url, b"fixture", "heading: Fixture")

        async def request(self, *args, **kwargs):
            return {"status": 200, "headers": {"location": "/invoices/fresh"}, "body": {}}

        async def perform(self, sid, action, target, args, op=None):
            calls.append((sid, args["url"]))
            self.page.url = args["url"]
            self.ui_actions += 1

    class Admin:
        async def state(self, tenant):
            return {}

    async def emit(*args):
        pass

    monkeypatch.setattr("myelin.program.executor.verify", lambda *args: [])
    result = await Executor(Admin(), emit)(
        program, {"amount_cents": 50001}, EnvironmentSpec(app="crm", revision="crm-v1"), Session()
    )
    assert result.success and result.model_calls == 0
    assert calls == [("single-common-suffix", "http://localhost:8101/invoices/fresh")]
