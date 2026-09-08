"""Independent business checks from the locked Phase 0 specification."""

from myelin.schema import AssertionResult


def verify(workflow, before_state, after_state, inputs, policy):
    if workflow != "crm.create_invoice":
        raise ValueError("unsupported oracle workflow")
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
    for table in ("customers", "invoices", "payments"):
        before = {r["id"]: r for r in before_state[table]}
        after = {r["id"]: r for r in after_state[table]}
        check(f"{table}.unrelated_unchanged", before, {key: after.get(key) for key in before})
        new[table] = [r for key, r in after.items() if key not in before]
    check("customers.no_additions", 0, len(new["customers"]))
    check("invoices.exactly_one", 1, len(new["invoices"]))
    check("payments.exactly_one", 1, len(new["payments"]))
    customers = [c for c in before_state["customers"] if c["name"] == inputs["customer"]]
    check("customer.unique_seed_match", 1, len(customers))
    invoice = new["invoices"][0] if len(new["invoices"]) == 1 else {}
    payment = new["payments"][0] if len(new["payments"]) == 1 else {}
    expected = {
        "customer_id": customers[0]["id"] if len(customers) == 1 else "missing",
        "description": inputs["description"],
        "quantity": inputs["quantity"],
        "unit_price_cents": inputs["unit_price_cents"],
        "total_cents": inputs["quantity"] * inputs["unit_price_cents"],
        "due_date": inputs["due_date"],
        "status": "paid",
    }
    for key, value in expected.items():
        check(f"invoice.{key}", value, invoice.get(key))
    check("payment.invoice_relationship", invoice.get("id", "missing"), payment.get("invoice_id"))
    check("payment.amount_cents", expected["total_cents"], payment.get("amount_cents"))
    return assertions


def projection(state, inputs):
    customer = next(c for c in state["customers"] if c["name"] == inputs["customer"])
    invoices = [i for i in state["invoices"] if i["customer_id"] == customer["id"]]
    return [
        {
            "customer": customer["name"],
            "invoice": {k: v for k, v in i.items() if k not in ("id", "tenant", "customer_id")},
            "payments": [
                {"amount_cents": p["amount_cents"]}
                for p in state["payments"]
                if p["invoice_id"] == i["id"]
            ],
        }
        for i in invoices
    ]
