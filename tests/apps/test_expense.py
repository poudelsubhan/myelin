from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from apps.expense.app import create_app
from myelin.adapters.expense import revision
from myelin.config import Settings
from myelin.verification.expense import verify

ADMIN = {"X-Myelin-Demo-Token": "private-test"}
INPUT = {
    "merchant": 'O\'Brien "Travel"',
    "amount_cents": 50001,
    "category": "Travel",
    "date": "2026-09-08",
    "receipt_text": 'Receipt "A"',
}


@pytest.fixture
def expense(tmp_path):
    settings = replace(
        Settings.load(),
        demo_token="private-test",
        demo_email="test@test.local",
        demo_password="synthetic",
    )
    with TestClient(create_app(tmp_path / "expense.sqlite", settings)) as client:
        yield client


def login(client, tenant="one", mutations=()):
    env = {
        "app": "expense",
        "revision": revision(mutations),
        "mutations": list(mutations),
        "seed_version": "expense-seed-v1",
    }
    assert (
        client.post(
            "/__reset",
            headers=ADMIN,
            json={"tenant": tenant, "seed_version": "expense-seed-v1", "environment": env},
        ).status_code
        == 200
    )
    response = client.post(
        "/api/login", json={"tenant": tenant, "email": "test@test.local", "password": "synthetic"}
    )
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}


def state(client, tenant="one"):
    return client.get("/__state", headers=ADMIN, params={"tenant": tenant}).json()


def test_tenant_isolation_idempotency_reconciliation_and_oracle(expense):
    headers = login(expense)
    before = state(expense)
    write = headers | {"X-Myelin-Operation-ID": "create"}
    row = expense.post("/api/expenses", headers=write, json=INPUT).json()
    assert expense.post("/api/expenses", headers=write, json=INPUT).json() == row
    assert (
        expense.post(
            "/api/expenses", headers=write, json=INPUT | {"merchant": "changed"}
        ).status_code
        == 409
    )
    other = login(expense, "two")
    assert expense.get("/api/expenses/" + row["id"], headers=other).status_code == 404
    assert expense.get("/api/operations/create", headers=other).json()["status"] == "absent"
    submit = "/api/expenses/" + row["id"] + "/submit"
    identity = headers | {"X-Myelin-Operation-ID": "submit"}
    assert expense.post(submit, headers=identity, json={}).status_code == 200
    assert expense.post(submit, headers=identity, json={}).status_code == 200
    assert (
        expense.post(
            submit, headers=headers | {"X-Myelin-Operation-ID": "duplicate"}, json={}
        ).status_code
        == 409
    )
    assert expense.get("/api/operations/submit", headers=headers).json()["status"] == "applied"
    assert all(
        a.passed for a in verify(before, state(expense), INPUT, {"revision": "expense-policy-v1"})
    )
    assert not all(
        a.passed
        for a in verify(before, state(expense), INPUT, {"revision": "expense-policy-manager-v2"})
    )


def test_mutations_reject_before_write_and_throttle_once(expense):
    headers = login(
        expense, mutations=("rename_field:amount", "add_step:confirm_submit", "throttle:list")
    )
    assert expense.get("/api/expenses", headers=headers).status_code == 429
    assert expense.get("/api/expenses", headers=headers).status_code == 200
    headers["X-Myelin-Operation-ID"] = "create"
    assert expense.post("/api/expenses", headers=headers, json=INPUT).status_code == 422
    assert state(expense)["expenses"] == []
    body = dict(INPUT)
    body["cost_cents"] = body.pop("amount_cents")
    row = expense.post("/api/expenses", headers=headers, json=body).json()
    headers["X-Myelin-Operation-ID"] = "submit"
    url = "/api/expenses/" + row["id"] + "/submit"
    assert expense.post(url, headers=headers, json={}).status_code == 422
    assert state(expense)["submissions"] == []
    assert (
        expense.post(
            url, headers=headers, json={"confirmation_token": row["confirmation_token"]}
        ).status_code
        == 200
    )


@pytest.mark.parametrize("bad", [True, -1, 1.25, "50001"])
def test_strict_cents(expense, bad):
    headers = login(expense) | {"X-Myelin-Operation-ID": "invalid"}
    assert (
        expense.post(
            "/api/expenses", headers=headers, json=INPUT | {"amount_cents": bad}
        ).status_code
        == 422
    )
    assert state(expense)["expenses"] == []
