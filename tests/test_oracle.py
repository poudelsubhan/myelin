from copy import deepcopy

import pytest

from myelin.verification.oracle import verify

INPUT = {
    "customer": "Acme Labs",
    "description": 'Quoted "café"',
    "quantity": 3,
    "unit_price_cents": 1999,
    "due_date": "2028-02-29",
}
BEFORE = {"customers": [{"id": "c", "name": "Acme Labs"}], "invoices": [], "payments": []}
AFTER = {
    "customers": BEFORE["customers"],
    "invoices": [
        {
            "id": "i",
            "customer_id": "c",
            "description": INPUT["description"],
            "quantity": 3,
            "unit_price_cents": 1999,
            "total_cents": 5997,
            "due_date": INPUT["due_date"],
            "status": "paid",
        }
    ],
    "payments": [{"id": "p", "invoice_id": "i", "amount_cents": 5997}],
}


def test_independent_correct_outcome():
    assert all(a.passed for a in verify("crm.create_invoice", BEFORE, AFTER, INPUT, {}))


@pytest.mark.parametrize(
    "mutation", ["amount", "missing_payment", "duplicate", "unrelated", "relation"]
)
def test_bad_business_outcomes_rejected(mutation):
    after = deepcopy(AFTER)
    if mutation == "amount":
        after["invoices"][0]["total_cents"] += 1
    elif mutation == "missing_payment":
        after["payments"] = []
    elif mutation == "duplicate":
        after["invoices"].append(after["invoices"][0] | {"id": "duplicate"})
    elif mutation == "unrelated":
        after["customers"][0]["name"] = "changed"
    else:
        after["payments"][0]["invoice_id"] = "wrong-invoice"
    assert not all(a.passed for a in verify("crm.create_invoice", BEFORE, after, INPUT, {}))
