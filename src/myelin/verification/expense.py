"""Independent expense oracle; policy is server-owned, never model authored."""

from myelin.schema import AssertionResult

POLICIES = {"expense-policy-v1", "expense-policy-manager-v2"}


def verify(before_state, after_state, inputs, policy):
    if policy["revision"] not in POLICIES:
        raise ValueError("unknown expense policy")
    assertions = []

    def check(name, expected, observed):
        assertions.append(
            AssertionResult(
                name=name,
                expected=expected,
                observed=observed,
                passed=type(expected) is type(observed) and expected == observed,
            )
        )

    new = {}
    for table in ("expenses", "submissions"):
        before = {r["id"]: r for r in before_state[table]}
        after = {r["id"]: r for r in after_state[table]}
        check(table + ".unrelated_unchanged", before, {k: after.get(k) for k in before})
        new[table] = [r for k, r in after.items() if k not in before]
        check(table + ".exactly_one", 1, len(new[table]))
    expense = new["expenses"][0] if len(new["expenses"]) == 1 else {}
    submission = new["submissions"][0] if len(new["submissions"]) == 1 else {}
    for key in ("merchant", "amount_cents", "category", "date", "receipt_text"):
        check("expense." + key, inputs[key], expense.get(key))
    check("expense.status", "submitted", expense.get("status"))
    check("submission.relationship", expense.get("id", "missing"), submission.get("expense_id"))
    note = (
        "approved by demo"
        if policy["revision"] == "expense-policy-manager-v2" and inputs["amount_cents"] > 50000
        else ""
    )
    check("submission.manager_note", note, submission.get("manager_note"))
    return assertions


def projection(state, inputs):
    return [
        {
            "expense": {k: v for k, v in row.items() if k not in ("id", "tenant")},
            "submissions": [
                {"manager_note": s["manager_note"]}
                for s in state["submissions"]
                if s["expense_id"] == row["id"]
            ],
        }
        for row in state["expenses"]
    ]
