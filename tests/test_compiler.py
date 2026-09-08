from pathlib import Path
from types import SimpleNamespace

import pytest

from myelin.program.bindings import BindingError
from myelin.program.compiler import Compiler, validate_program
from myelin.program.lifter import lift
from myelin.schema import LiteralRef, NamedRef, NetworkEvent, Program, Trace
from myelin.trace.store import TraceStore


def fixture():
    root = Path("tests/fixtures/lifter")
    trace = Trace.model_validate_json((root / "crm-trace.json").read_text())
    network = [
        NetworkEvent.model_validate_json(line)
        for line in (root / "crm-network.jsonl").read_text().splitlines()
    ]
    candidates = lift(trace, trace.inputs, network)
    program = Program.model_validate_json(
        Path(
            "programs/crm.create_invoice/5d78fefeb7883b44ccd1bfa1790dbac143bb1d867921dda7f31cbd70f0744253/program.json"
        ).read_text()
    )
    return trace, candidates, program


def test_compiler_validates_provenance_fresh_bindings_and_observed_http():
    trace, candidates, program = fixture()
    assert validate_program(program, trace, candidates, {"http://localhost:8101"}) == program
    bad = program.model_copy(deep=True)
    bad.steps[0].source_action_ids = ["invented-action"]
    with pytest.raises(BindingError, match="source action"):
        validate_program(bad, trace, candidates, {"http://localhost:8101"})
    bad = program.model_copy(deep=True)
    bad.steps[1].arguments["value"] = NamedRef(kind="variable", key="not_defined")
    with pytest.raises(BindingError, match="unavailable"):
        validate_program(bad, trace, candidates, {"http://localhost:8101"})
    bad = program.model_copy(deep=True)
    bad.steps[-1].url = LiteralRef(value="http://localhost:8101/__reset")
    with pytest.raises(BindingError, match="observed candidate"):
        validate_program(bad, trace, candidates, {"http://localhost:8101"})


async def test_json_correction_loop_is_bounded_and_validates_before_return(monkeypatch, tmp_path):
    trace, candidates, program = fixture()
    outputs = iter(['{"invalid":true}', program.model_dump_json()])

    class FakeAstra:
        def __init__(self, *args):
            pass

        async def respond(self, *args, **kwargs):
            return SimpleNamespace(id="compiler-response", output_text=next(outputs))

    monkeypatch.setattr("myelin.program.compiler.Astra", FakeAstra)
    events = []

    async def emit(kind, payload):
        events.append(kind)

    compiler = Compiler(
        SimpleNamespace(crm_url="http://localhost:8101"), TraceStore(tmp_path, "compile"), emit
    )
    assert (await compiler(trace, candidates)).content_hash() == program.content_hash()
    assert events == ["program.compiled"]


async def test_injected_provider_runs_before_json_validation(monkeypatch, tmp_path):
    trace, candidates, program = fixture()
    call = SimpleNamespace(
        type="function_call", name="inspect_evidence", call_id="original-call", async_=False
    )
    seen = []

    class FakeAstra:
        def __init__(self, *args):
            pass

        async def respond(self, *args, **kwargs):
            seen.append(kwargs)
            if len(seen) == 1:
                return SimpleNamespace(id="tool-response", output=[call], output_text="")
            return SimpleNamespace(
                id="json-response", output=[], output_text=program.model_dump_json()
            )

    class Provider:
        def tools(self):
            return [{"type": "function", "name": "inspect_evidence"}]

        async def execute(self, c):
            assert c.call_id == "original-call"
            return [
                {"type": "function_call_output", "call_id": c.call_id, "output": "fixture evidence"}
            ]

    async def emit(*args):
        pass

    monkeypatch.setattr("myelin.program.compiler.Astra", FakeAstra)
    compiler = Compiler(
        SimpleNamespace(crm_url="http://localhost:8101"), TraceStore(tmp_path, "provider"), emit
    )
    result = await compiler(trace, candidates, tool_provider=Provider())
    assert result == program
    assert seen[1]["previous_response_id"] == "tool-response"
    assert seen[1]["input"][0]["call_id"] == "original-call"
