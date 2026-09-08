import pytest

from myelin.gate.gate import GateRunner, suite
from tests.test_gate import setup_gate


@pytest.mark.parametrize("fault", [None, "mismatch", "missing", "no_model"])
async def test_full_reference_requires_eight_real_successes_and_matches(tmp_path, fault):
    candidates, program, runner, gate, events = await setup_gate(tmp_path)

    async def reference(p, inputs, environment, run_id):
        if fault == "missing":
            raise TimeoutError()
        result = await runner.run_program(p, inputs, environment, run_id)
        result.model_calls = 0 if fault == "no_model" else 1
        return (
            result,
            {"business": "wrong" if fault == "mismatch" else inputs},
            [run_id + "-response"],
        )

    async def outcome(p, inputs, run_id):
        return {"business": inputs}

    full = GateRunner(
        tmp_path / "full", candidates, runner.run_program, runner.emit, reference, outcome
    )
    request = gate.request.model_copy(
        update={"reference_mode": "full", "reference_case_ids": [c.case_id for c in suite()]}
    )
    result = await full(request)
    assert result.reference_total == 8
    assert (result.status == "passed") == (fault is None)
    assert result.reference_passes == (8 if fault is None else 0)
    assert all(c.reference_run_id != c.program_run_id for c in result.cases)
    with pytest.raises(ValueError, match="coverage"):
        await full(
            request.model_copy(update={"reference_case_ids": request.reference_case_ids[:-1]})
        )
