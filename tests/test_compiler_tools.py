import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from myelin.astra.compiler_tools import CompilerTools
from myelin.schema import Program
from myelin.trace.store import TraceStore


async def test_staged_update_is_required_and_hosted_output_is_never_executed(tmp_path):
    prior = Program.model_validate_json(Path("tests/fixtures/crm-ui-program.json").read_text())

    async def emit(*args):
        pass

    provider = CompilerTools(None, TraceStore(tmp_path, "provider"), emit, prior)
    with pytest.raises(ValueError, match="no completed"):
        provider.validate_output(provider.staging.program())
    provider.patch_calls = [{"status": "completed"}]
    with pytest.raises(ValueError, match="changed staged"):
        provider.validate_output(provider.staging.program())
    with pytest.raises(ValueError, match="service-owned"):
        await provider.execute(SimpleNamespace(type="shell_call_output"))
    call = SimpleNamespace(
        type="function_call",
        name="read_candidate",
        call_id="read1",
        arguments=json.dumps({"path": "../oracle.py"}),
    )
    assert (await provider.execute(call))[0]["output"] == "Path is not allowed"
    with pytest.raises(ValueError, match="duplicate"):
        await provider.execute(call)
    changed = provider.staging.program()
    changed.notes = "Observed trace analysis"
    (provider.staging.root / "program.json").write_text(changed.model_dump_json())
    assert provider.validate_output(changed) == changed
    with pytest.raises(ValueError, match="equal"):
        provider.validate_output(prior)
