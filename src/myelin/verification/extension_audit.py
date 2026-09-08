"""Audit saved extension evidence against immutable programs and actual run artifacts."""

import hashlib
import json

from myelin.config import ROOT
from myelin.gate.ledger import CandidateStore
from myelin.program.repair_scope import validate_scope
from myelin.schema import GateResult, Trace
from myelin.steer.policy import TEXT, parse


def audit_extensions(evidence, settings):
    root = settings.runs_dir
    candidates = CandidateStore(ROOT / "programs")

    def read(folder, name):
        return json.loads((root / folder / name).read_text())

    def lines(folder, name):
        return [json.loads(line) for line in (root / folder / name).read_text().splitlines()]

    gates = {}

    def gate(gid):
        g = GateResult.model_validate(read("gates/" + gid, "result.json"))
        assert g.status == "passed" and g.oracle_passes == g.reference_passes == 8
        assert g.oracle_total == g.reference_total == 8 and g.all_references_complete
        assert g.request.reference_mode == "full"
        pairs = []
        for case in g.cases:
            assert (
                case.program_success and case.reference_status == "success" and case.outcome_match
            )
            assert all(a.passed for a in case.assertions + case.differences)
            program = read(case.program_run_id, "result.json")
            trace = Trace.model_validate(read(case.reference_run_id, "trace.json"))
            usage = lines(case.reference_run_id, "usage.jsonl")
            assert program["success"] and program["model_calls"] == 0
            assert trace.outcome == "success" and trace.environment == g.request.environment
            assert trace.policy_revision == g.request.policy_revision and trace.usage_response_ids
            assert set(trace.usage_response_ids) <= {u["response_id"] for u in usage}
            pairs.append(
                {
                    "case_id": case.case_id,
                    "program_run_id": case.program_run_id,
                    "reference_run_id": case.reference_run_id,
                    "reference_response_ids": trace.usage_response_ids,
                }
            )
        gates[gid] = {"candidate_hash": g.request.candidate_hash, "paired_cases": pairs}
        return g

    expense = evidence["expense"]
    assert expense["passed"]
    old = candidates.get("expense.submit_expense", expense["baseline_hash"])
    new = candidates.get("expense.submit_expense", expense["steered_hash"])
    assert new.steps[:2] == old.steps[:2]
    assert all(s.kind == "http" for s in new.steps[:2])
    branch = next(s for s in new.steps if s.kind == "branch")
    assert branch.condition == parse(TEXT)
    steered_id = expense["runs"]["steered"]["run_id"]
    trace = Trace.model_validate(read(steered_id, "trace.json"))
    mark = next(m for m in trace.steer_marks if m.id == branch.source_steer_id)
    assert mark.accepted and mark.text == TEXT and mark.action_id and mark.observation_id
    socket = lines(steered_id, "websocket.jsonl")
    assert any(
        e["type"] == "response.steer.accepted" and e["steer"]["id"] == mark.id for e in socket
    )
    assert (root / steered_id / "observations" / (mark.observation_id + ".png")).is_file()
    gate(expense["gate_id"])
    coverage = read("gates/" + expense["gate_id"], "coverage.json")[branch.id]
    assert coverage["true"] and coverage["false"] and coverage["boundary"] == ["expense-2"]
    ninth = read(expense["runs"]["ninth"]["run_id"], "result.json")
    assert ninth["success"] and ninth["model_calls"] == 0

    native = evidence["native"]
    assert native["terminal_deliveries"] == 1 and native["pending_during_analysis"]
    assert native["effort_update_accepted"] and native["status"] == "passed"
    call = read(native["run_id"], "native-call.json")
    assert call["call_id"] == native["native_call_id"] and call["async_"]
    assert read(native["run_id"], "independent-analysis.json")["pending_during_analysis"]
    terminal = [
        row
        for row in lines(native["run_id"], "async-tools.jsonl")
        if row.get("type") == "function_call_output"
    ]
    assert len(terminal) == 1 and terminal[0]["call_id"] == native["native_call_id"]
    assert any(
        row["request_effort"] == "medium" and row["effective_effort"] == "high" and row["updates"]
        for row in lines(native["run_id"], "effort.jsonl")
    )
    gate(native["gate_id"])

    hosted = evidence["hosted"]
    assert hosted["passed"] and hosted["decision"]["verdict"] == "promoted"
    metadata = read(hosted["run_id"], "hosted-container.json")
    upload = root / hosted["run_id"] / "hosted-upload.json"
    # TraceStore pretty-prints JSON; hash refers to the exact uploaded encoding.
    encoded = json.dumps(json.loads(upload.read_text()), ensure_ascii=False).encode()
    assert hashlib.sha256(encoded).hexdigest() == metadata["sha256"]
    assert metadata["container_id"] == hosted["container_id"]
    for name, rid in zip(
        ("hosted-analysis-response.json", "hosted-followup-response.json"),
        hosted["hosted_response_ids"],
        strict=True,
    ):
        response = read(hosted["run_id"], name)
        assert response["id"] == rid
        assert any(o["type"] == "shell_call_output" for o in response["output"])
    patches = lines(hosted["run_id"], "patches.jsonl")
    assert len({p["result"]["call_id"] for p in patches}) == len(patches)
    assert any(p["status"] == "completed" for p in hosted["patch_calls"])
    parent = candidates.get("crm.create_invoice", hosted["source_hash"])
    child = candidates.get("crm.create_invoice", hosted["candidate_hash"])
    assert child.steps == parent.steps and child.final_post == parent.final_post
    gate(hosted["gate_id"])

    assert set(evidence["repairs"]) == {"crm", "expense"}
    for app, repair in evidence["repairs"].items():
        assert repair["passed"]
        workflow = app + (".create_invoice" if app == "crm" else ".submit_expense")
        parent = candidates.get(workflow, repair["original_hash"])
        child = candidates.get(workflow, repair["candidate_hash"])
        failure = read(repair["repair_run_id"], "failure-before-repair.json")
        assert failure["failure"]["effect_status"] == "not_applied"
        validate_scope(child, parent, [failure["failed_step_id"]])
        events = lines(repair["repair_run_id"], "events.jsonl")
        assert any(
            e["kind"] == "ledger.updated"
            and e["payload"]["mode"] == "restoration"
            and e["payload"]["verdict"] == "promoted"
            for e in events
        )
        result = read(repair["repair_run_id"], "result.json")
        assert result["success"] and all(a["passed"] for a in result["oracle_assertions"])
        replay = read(repair["replay_run_id"], "result.json")
        assert replay["success"] and replay["model_calls"] == 0
        gate(repair["gate_id"])
    negatives = evidence["negatives"]
    assert negatives["passed"] and set(negatives["cases"]) == {
        "wrong_comparator",
        "duplicate_submit",
    }
    for row in negatives["cases"].values():
        assert row["decision"]["verdict"] == "rejected"
        assert read("gates/" + row["gate_id"], "result.json")["status"] == "failed"
    return {
        "passed": True,
        "criteria": {f"E{i}": True for i in range(1, 6)},
        "steer_id": mark.id,
        "predicate": branch.condition.model_dump(mode="json"),
        "coverage": coverage,
        "gates": gates,
    }
