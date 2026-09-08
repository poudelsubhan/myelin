from pathlib import Path

import pytest

from myelin.gate.gate import GateRunner, suite, suite_hash
from myelin.gate.ledger import CandidateStore, Ledger
from myelin.schema import (
    AssertionResult,
    EnvironmentSpec,
    GateRequest,
    Program,
    RunResult,
)


async def setup_gate(tmp_path, work=50):
    program = Program.model_validate_json(Path("tests/fixtures/crm-ui-program.json").read_text())
    candidates = CandidateStore(tmp_path / "programs")
    digest = candidates.put(program)
    env = EnvironmentSpec(app="crm", revision="crm-v1")
    request = GateRequest(
        candidate_hash=digest,
        workflow=program.workflow,
        environment=env,
        policy_revision=program.policy_revision,
        suite_hash=suite_hash(),
        reference_mode="none",
        reference_case_ids=[],
    )
    events = []

    async def emit(kind, payload):
        events.append(kind)

    async def execute(p, inputs, environment, run_id):
        return RunResult(
            run_id=run_id,
            success=True,
            status="completed",
            program_hash=p.content_hash(),
            environment=environment,
            final_observation="o",
            oracle_assertions=[
                AssertionResult(name="independent.fixture", passed=True, expected=1, observed=1)
            ],
            model_calls=0,
            model_usd="0",
            wall_ms=1,
            http_requests=work,
            ui_actions=0,
            work_units=work,
        )

    runner = GateRunner(tmp_path / "gates", candidates, execute, emit)
    gate = await runner(request)
    return candidates, program, runner, gate, events


async def test_gate_exact_counts_and_incomplete_reference_rejected(tmp_path):
    _, _, runner, gate, events = await setup_gate(tmp_path)
    assert (gate.oracle_passes, gate.oracle_total, gate.reference_total) == (8, 8, 0)
    assert events.count("gate.case") == 8
    with pytest.raises(ValueError, match="Phase 5"):
        await runner(gate.request.model_copy(update={"reference_mode": "full"}))
    with pytest.raises(ValueError, match="locked"):
        await runner(gate.request.model_copy(update={"suite_hash": "changed"}))


async def test_ledger_stale_parent_partial_evidence_and_costlier_candidate(tmp_path):
    candidates, program, runner, gate, _ = await setup_gate(tmp_path)
    ledger = Ledger(tmp_path / "ledger", candidates)
    ids = [c.case_id for c in suite()]
    row = await ledger.promote(gate, None, "initial", ids)
    assert row.verdict == "promoted"
    digest = program.content_hash()
    child = program.model_copy(deep=True)
    child.parent_hash = digest
    child.version = 2
    child.notes = "candidate"
    candidate_hash = candidates.put(child)
    next_gate = await runner(gate.request.model_copy(update={"candidate_hash": candidate_hash}))
    partial = next_gate.model_copy(update={"cases": next_gate.cases[:-1]})
    assert (
        await ledger.promote(partial, digest, "optimization", ids, baseline=gate)
    ).verdict == "rejected"
    costly = next_gate.model_copy(update={"max_work_units": 51})
    decision = await ledger.promote(costly, digest, "optimization", ids, baseline=gate)
    assert decision.rule == "non_regression" and decision.verdict == "rejected"
    assert (
        await ledger.promote(next_gate, "stale", "optimization", ids, baseline=gate)
    ).rule == "stale_parent"
    assert ledger.current(program.workflow)["candidate_hash"] == digest
    assert (
        await ledger.promote(next_gate, digest, "optimization", ids, baseline=gate)
    ).verdict == "promoted"
    assert (
        await ledger.promote(next_gate, digest, "optimization", ids, baseline=gate)
    ).rule == "stale_parent"


async def test_immutable_bytes_and_wrong_environment(tmp_path):
    candidates, program, _, gate, _ = await setup_gate(tmp_path)
    ledger = Ledger(tmp_path / "ledger", candidates)
    bad = gate.model_copy(deep=True)
    bad.request.environment.revision = "crm-other"
    row = await ledger.promote(bad, None, "initial", [c.case_id for c in suite()])
    assert row.verdict == "rejected"
    path = candidates.root / program.workflow / program.content_hash() / "program.json"
    path.write_text(path.read_text().replace('"notes":', '"notes":"tampered", "ignored":'))
    with pytest.raises(ValueError):
        candidates.get(program.workflow, program.content_hash())
