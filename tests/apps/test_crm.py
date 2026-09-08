import re

import pytest
from fastapi.testclient import TestClient

from apps.crm.app import revision

ADMIN = {"X-Myelin-Demo-Token": "test-admin-only"}


def reset(client, tenant="a", mutations=()):
    env = {
        "app": "crm",
        "revision": revision(mutations),
        "mutations": list(mutations),
        "seed_version": "crm-seed-v1",
    }
    return client.post(
        "/__reset",
        headers=ADMIN,
        json={"tenant": tenant, "seed_version": "crm-seed-v1", "environment": env},
    )


def login(client, tenant="a"):
    return client.post(
        "/login",
        data={"email": "demo@test.local", "password": "synthetic-password", "tenant": tenant},
        follow_redirects=False,
    )


def state(client, tenant="a"):
    return client.get("/__state", params={"tenant": tenant}, headers=ADMIN).json()


def token(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def form(client, tenant="a"):
    customer = state(client, tenant)["customers"][0]["id"]
    page = client.get(f"/customers/{customer}/invoices/new")
    return f"/customers/{customer}/invoices", {
        "csrf": token(page.text),
        "operation_id": "create-1",
        "description": 'Quote " & café',
        "quantity": "3",
        "unit_price_cents": "1999",
        "total_cents": "5997",
        "due_date": "2028-02-29",
    }


def test_auth_and_admin_boundaries(client):
    assert client.get("/__state?tenant=a").status_code == 403
    assert client.post("/__reset", json={}).status_code in (403, 422)
    assert reset(client).status_code == 200
    assert client.get("/customers").status_code == 401
    assert client.get("/customers/id/invoices/new").status_code == 401
    assert client.post("/login", data={"email": "bad"}).status_code == 401
    assert login(client).status_code == 303
    assert client.cookies.get("myelin_session")
    assert len(state(client)["customers"]) == 5


def test_csrf_total_and_date_reject_before_writes(client):
    reset(client)
    login(client)
    url, data = form(client)
    for bad in (
        {"csrf": "bad"},
        {"total_cents": "5998"},
        {"quantity": "3.0"},
        {"unit_price_cents": "-1"},
        {"due_date": "2027-02-29"},
        {"due_date": "20280229"},
        {"description": ""},
    ):
        assert client.post(url, data=data | bad).status_code in (403, 422)
        assert state(client)["invoices"] == []
    assert client.get("/api/operations/create-1").json()["status"] == "absent"


def test_atomic_idempotency_reconciliation_and_conflict(client):
    reset(client)
    login(client)
    url, data = form(client)
    first = client.post(url, data=data, follow_redirects=False)
    assert first.status_code == 303
    second = client.post(url, data=data, follow_redirects=False)
    assert second.headers["location"] == first.headers["location"]
    assert len(state(client)["invoices"]) == 1
    status = client.get("/api/operations/create-1").json()
    assert status["status"] == "applied"
    assert status["result"]["location"] == first.headers["location"]
    assert status["entity_id"] == state(client)["invoices"][0]["id"]
    assert client.post(url, data=data | {"description": "different"}).status_code == 409
    page = client.get(first.headers["location"])
    pay = {"csrf": token(page.text), "operation_id": "pay-1"}
    pay_url = first.headers["location"] + "/pay"
    assert client.post(pay_url, data=pay, follow_redirects=False).status_code == 303
    assert client.post(pay_url, data=pay, follow_redirects=False).status_code == 303
    assert client.post(pay_url, data=pay | {"operation_id": "pay-2"}).status_code == 409
    final = state(client)
    assert len(final["payments"]) == 1
    assert final["payments"][0]["amount_cents"] == 5997
    assert final["invoices"][0]["status"] == "paid"


def test_fresh_csrf_and_session_binding(client):
    reset(client)
    login(client)
    url, data = form(client)
    _, fresh = form(client)
    assert data["csrf"] != fresh["csrf"]
    assert client.post(url, data=data).status_code == 403
    login(client)
    assert client.post(url, data=fresh).status_code == 403


def test_tenant_isolation_and_same_operation_ids(client):
    reset(client, "a")
    reset(client, "b")
    login(client, "a")
    url, data = form(client)
    created = client.post(url, data=data, follow_redirects=False).headers["location"]
    with TestClient(client.app) as other:
        login(other, "b")
        assert other.get(created).status_code == 404
        assert other.get(url + "/new").status_code == 404
        assert other.get("/api/operations/create-1").json()["status"] == "absent"
        b_url, b_data = form(other, "b")
        assert other.post(url, data=b_data).status_code == 404
        assert other.post(b_url, data=b_data, follow_redirects=False).status_code == 303
        assert len(state(other, "b")["invoices"]) == 1
    assert len(state(client, "a")["invoices"]) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "rename_field:invoice_total",
        "reorder_fields:invoice",
        "move_button:mark_paid",
        "add_modal:consent",
    ],
)
def test_exact_mutation_reset_and_revision(client, mutation):
    assert reset(client, mutations=[mutation]).status_code == 200
    login(client)
    url, data = form(client)
    if mutation == "rename_field:invoice_total":
        assert client.post(url, data=data).status_code == 422
        assert state(client)["invoices"] == []
        data["amount_due_cents"] = data.pop("total_cents")
    assert client.post(url, data=data, follow_redirects=False).status_code == 303
    assert state(client)["environment"]["mutations"] == [mutation]
    assert reset(client).status_code == 200
    assert state(client)["environment"]["mutations"] == []
    assert state(client)["environment"]["revision"] == "crm-v1"
    assert state(client)["invoices"] == []
    assert client.get("/customers").status_code == 401


def test_operation_header_precedence(client):
    reset(client)
    login(client)
    url, data = form(client)
    assert (
        client.post(
            url, data=data, headers={"X-Myelin-Operation-ID": "runtime-op"}, follow_redirects=False
        ).status_code
        == 303
    )
    assert client.get("/api/operations/runtime-op").json()["status"] == "applied"
    assert client.get("/api/operations/create-1").json()["status"] == "absent"


def test_chaos_validation(client):
    reset(client)
    body = {"tenant": "a", "mutation": "rename_field:invoice_total", "on": True}
    assert client.post("/__chaos", json=body).status_code == 403
    changed = client.post("/__chaos", headers=ADMIN, json=body)
    assert changed.status_code == 200
    assert changed.json()["revision"] == revision([body["mutation"]])
    assert (
        client.post("/__chaos", headers=ADMIN, json=body | {"mutation": "not-real"}).status_code
        == 422
    )
