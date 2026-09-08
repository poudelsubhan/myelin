from scripts.full_demo import replay_request


def test_budget_rehearsal_has_no_record_repair_compile_or_reference_path():
    body = replay_request(
        "expense.submit_expense", "a" * 64, {"app": "expense"}, "policy", {"amount_cents": 50001}
    )
    assert body["mode"] == "program"
    assert body["compile_after"] is False and body["full_validation"] is False
    assert body["program_hash"] == "a" * 64
    assert body["inputs"]["amount_cents"] == 50001
