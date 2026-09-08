"""Audit actual core artifacts; stage labels alone are never passing evidence."""

import json

from myelin.schema import GateResult, NetworkEvent, Program, Trace, UsageRecord


def audit_core(evidence, settings):
    runs = evidence["runs"]
    learning = settings.runs_dir / runs["learn"]["run_id"]
    trace = Trace.model_validate_json((learning / "trace.json").read_text())
    assert trace.outcome == "success" and trace.actions
    observations = {
        json.loads(line)["id"]: json.loads(line)
        for line in (learning / "observations.jsonl").read_text().splitlines()
    }
    assert trace.initial_observation in observations
    networks = {
        n.request_id: n
        for line in (learning / "network.jsonl").read_text().splitlines()
        if (n := NetworkEvent.model_validate_json(line))
    }
    assert len({a.id for a in trace.actions}) == len(trace.actions)
    for action in trace.actions:
        assert action.before_id in observations and action.after_id in observations
        assert all(r in networks for r in action.request_ids)
        for oid in (action.before_id, action.after_id):
            assert (learning / observations[oid]["frame_path"]).is_file()
    usage = {
        u.response_id: u
        for line in (learning / "usage.jsonl").read_text().splitlines()
        if (u := UsageRecord.model_validate_json(line))
    }
    assert set(trace.usage_response_ids) <= usage.keys()
    assert all(usage[r].model_calls == 1 for r in trace.usage_response_ids)
    old_hash = runs["learn"]["candidate_hash"]
    new_hash = runs["repair"]["candidate_hash"]
    root = settings.runs_dir.parent / "programs" / "crm.create_invoice"
    original = Program.model_validate_json((root / old_hash / "program.json").read_text())
    repaired = Program.model_validate_json((root / new_hash / "program.json").read_text())
    assert original.content_hash() == old_hash and repaired.content_hash() == new_hash
    assert repaired.parent_hash == old_hash
    assert [s.id for s in original.steps] == [s.id for s in repaired.steps]
    for old, new in zip(original.steps, repaired.steps, strict=True):
        if old.id != "invoice-write":
            assert old == new
    repair_folder = settings.runs_dir / runs["repair"]["run_id"]
    failure = json.loads((repair_folder / "failure-before-repair.json").read_text())
    assert failure["failed_step_id"] == "invoice-write"
    assert failure["failure"]["effect_status"] == "not_applied"
    assert failure["failure"]["observed"]["status"] == 422
    for label in ("learn", "repair"):
        gate = GateResult.model_validate_json(
            (settings.runs_dir / "gates" / runs[label]["gate_id"] / "result.json").read_text()
        )
        assert gate.status == "passed" and gate.oracle_passes == gate.oracle_total == 8
        assert gate.request.candidate_hash == runs[label]["candidate_hash"]
        assert all(c.program_success and all(a.passed for a in c.assertions) for c in gate.cases)
    for label in ("ninth", "presentation", "post_repair", "lost_response"):
        result = runs[label]["result"]
        assert result["success"] and result["model_calls"] == 0
        assert all(a["passed"] for a in result["oracle_assertions"])
    loss = settings.runs_dir / runs["lost_response"]["run_id"]
    events = [json.loads(line) for line in (loss / "events.jsonl").read_text().splitlines()]
    assert any(
        e["kind"] == "repair.reconciled" and e["payload"]["status"] == "applied" for e in events
    )
    assert {n["kind"] for n in evidence["negative_gates"]} == {
        "wrong_amount",
        "omitted_payment",
        "duplicate_write",
        "costlier",
    }
    for negative in evidence["negative_gates"]:
        assert negative["decision"]["verdict"] == "rejected"
        assert negative["gate"]["oracle_total"] == 8
        if negative["kind"] == "costlier":
            assert negative["gate"]["status"] == "passed"
            assert negative["decision"]["rule"] == "non_regression"
        else:
            assert negative["gate"]["status"] == "failed"
    return {
        "artifact_audit": "passed",
        "source_trace": trace.run_id,
        "original_hash": old_hash,
        "repaired_hash": new_hash,
    }
