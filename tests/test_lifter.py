import pytest

from myelin.program.bindings import BindingError, resolve
from myelin.program.lifter import bind_field


def test_repeated_values_need_field_identity():
    inputs = {"quantity": 42, "unit_price_cents": 42, "description": "42"}
    assert bind_field("quantity", "42", inputs).key == "quantity"
    assert bind_field("unit_price_cents", "42", inputs).key == "unit_price_cents"
    with pytest.raises(BindingError, match="ambiguous"):
        bind_field("unexplained", "42", inputs)
    derived = bind_field("total_cents", "1764", inputs)
    assert resolve(derived, inputs, {}, {}) == 1764
    with pytest.raises(BindingError):
        bind_field("total_cents", "1700", inputs)


def test_real_sanitized_trace_preserves_prerequisites_and_refuses_missing_bodies():
    from pathlib import Path

    from myelin.program.lifter import lift
    from myelin.schema import NetworkEvent, Trace

    root = Path(__file__).parent / "fixtures/lifter"
    trace = Trace.model_validate_json((root / "crm-trace.json").read_text())
    network = [
        NetworkEvent.model_validate_json(line)
        for line in (root / "crm-network.jsonl").read_text().splitlines()
    ]
    candidates = lift(trace, trace.inputs, network)
    assert [c.id for c in candidates] == [
        "invoice-form",
        "invoice-write",
        "payment-form",
        "payment-write",
    ]
    assert candidates[1].prerequisite_ids == ["invoice-form"]
    assert candidates[3].prerequisite_ids == ["payment-form"]
    assert candidates[1].step.body["csrf"].kind == "secret"
    assert candidates[2].step.url.key == "invoice_location"
    assert all(c.source_action_ids and c.request_ids for c in candidates)
    missing = [
        n.model_copy(update={"response_body": None, "body_omitted_reason": "unavailable"})
        for n in network
    ]
    assert lift(trace, trace.inputs, missing) == []
