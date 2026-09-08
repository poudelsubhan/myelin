"""Owned CRM with tenant isolation and transactional, reconcilable writes."""
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from myelin.config import ROOT, Settings
from myelin.schema import Contract, EnvironmentSpec

MUTATIONS = {
    "reorder_fields:invoice", "move_button:mark_paid", "rename_field:invoice_total",
    "add_modal:consent",
}
CUSTOMERS = ["Acme Labs", "Birch Studio", "Cedar Health", "Delta Works", "Élan Design"]


class Reset(Contract):
    tenant: str
    seed_version: str
    environment: EnvironmentSpec


class Chaos(Contract):
    tenant: str
    mutation: str
    on: bool


def revision(mutations):
    if not mutations:
        return "crm-v1"
    digest = hashlib.sha256(json.dumps(sorted(mutations)).encode()).hexdigest()[:12]
    return f"crm-v1-{digest}"


def create_app(db_path: Path | None = None, settings: Settings | None = None):
    settings = settings or Settings.load()
    db_path = db_path or ROOT / "apps/crm/data/crm.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="Myelin CRM")
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    @contextmanager
    def db():
        conn = sqlite3.connect(db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    with db() as conn:
        conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS tenants(tenant TEXT PRIMARY KEY, environment TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS customers(
            id TEXT PRIMARY KEY, tenant TEXT NOT NULL, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY, tenant TEXT NOT NULL, csrf TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS invoices(
            id TEXT PRIMARY KEY, tenant TEXT NOT NULL, customer_id TEXT NOT NULL,
            description TEXT NOT NULL, quantity INTEGER NOT NULL, unit_price_cents INTEGER NOT NULL,
            total_cents INTEGER NOT NULL, due_date TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payments(
            id TEXT PRIMARY KEY, tenant TEXT NOT NULL, invoice_id TEXT NOT NULL,
            amount_cents INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS operations(
            tenant TEXT NOT NULL, operation_id TEXT NOT NULL, request_hash TEXT NOT NULL,
            entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL, result TEXT NOT NULL,
            PRIMARY KEY(tenant,operation_id));
        """)

    def admin(request):
        supplied = request.headers.get("X-Myelin-Demo-Token", "")
        if not settings.demo_token or not hmac.compare_digest(supplied, settings.demo_token):
            raise HTTPException(403, "internal demo token required")

    def session(request, conn):
        row = conn.execute("SELECT * FROM sessions WHERE token=?",
                           (request.cookies.get("myelin_session", ""),)).fetchone()
        if row is None:
            raise HTTPException(401, "sign in required")
        return dict(row)

    def environment(conn, tenant):
        row = conn.execute("SELECT environment FROM tenants WHERE tenant=?", (tenant,)).fetchone()
        if row is None:
            raise HTTPException(404, "unknown tenant")
        return json.loads(row[0])

    def csrf_form(request, conn):
        s = session(request, conn)
        s["csrf"] = secrets.token_urlsafe(24)
        conn.execute("UPDATE sessions SET csrf=? WHERE token=?", (s["csrf"], s["token"]))
        return s

    def validate_csrf(s, form):
        value = form.get("csrf", "")
        if not isinstance(value, str) or not hmac.compare_digest(value, s["csrf"]):
            raise HTTPException(403, "invalid CSRF token")

    def scoped(conn, table, entity_id, tenant):
        # Table is selected by application code, never by user input.
        row = conn.execute(f"SELECT * FROM {table} WHERE id=? AND tenant=?",
                           (entity_id, tenant)).fetchone()
        if row is None:
            raise HTTPException(404, "record not found")
        return dict(row)

    def integer(form, key, minimum=0):
        value = form.get(key, "")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
            raise HTTPException(422, f"{key} must be integer cents or quantity")
        result = int(value)
        if result < minimum or result > 2**53 - 1:
            raise HTTPException(422, f"{key} out of bounds")
        return result

    def identity(request, form):
        operation_id = request.headers.get("X-Myelin-Operation-ID") or form.get("operation_id")
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 200:
            raise HTTPException(422, "operation identity required")
        return operation_id

    def replay(conn, tenant, operation_id, payload):
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        row = conn.execute("SELECT * FROM operations WHERE tenant=? AND operation_id=?",
                           (tenant, operation_id)).fetchone()
        if row and row["request_hash"] != digest:
            raise HTTPException(409, "operation identity reused with conflicting content")
        return digest, json.loads(row["result"]) if row else None

    def save_operation(conn, tenant, operation_id, digest, entity_id, result):
        conn.execute("INSERT INTO operations VALUES(?,?,?,?,?,?)",
                     (tenant, operation_id, digest, "invoice", entity_id, json.dumps(result)))

    @app.get("/health")
    async def health():
        return {"status": "ok", "app": "crm"}

    @app.post("/__reset")
    async def reset(body: Reset, request: Request):
        admin(request)
        env = body.environment
        if (body.seed_version != "crm-seed-v1" or env.seed_version != body.seed_version
                or env.app != "crm" or set(env.mutations) - MUTATIONS
                or env.revision != revision(env.mutations)):
            raise HTTPException(422, "unsupported seed/environment/revision")
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for table in ("operations", "payments", "invoices", "sessions", "customers", "tenants"):
                conn.execute(f"DELETE FROM {table} WHERE tenant=?", (body.tenant,))
            conn.execute("INSERT INTO tenants VALUES(?,?)", (body.tenant, env.model_dump_json()))
            for name in CUSTOMERS:
                conn.execute("INSERT INTO customers VALUES(?,?,?)",
                             (str(uuid4()), body.tenant, name))
        return {"tenant": body.tenant, "environment": env}

    @app.get("/__state")
    async def state(request: Request, tenant: str):
        admin(request)
        with db() as conn:
            result = {"environment": environment(conn, tenant)}
            for table in ("customers", "invoices", "payments"):
                result[table] = [dict(r) for r in conn.execute(
                    f"SELECT * FROM {table} WHERE tenant=? ORDER BY id", (tenant,))]
        return result

    @app.post("/__chaos")
    async def chaos(body: Chaos, request: Request):
        admin(request)
        if body.mutation not in MUTATIONS:
            raise HTTPException(422, "unknown mutation")
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            env = environment(conn, body.tenant)
            mutations = set(env["mutations"])
            if body.on:
                mutations.add(body.mutation)
            else:
                mutations.discard(body.mutation)
            env.update(mutations=sorted(mutations), revision=revision(mutations))
            conn.execute("UPDATE tenants SET environment=? WHERE tenant=?",
                         (json.dumps(env), body.tenant))
        return env

    @app.get("/login")
    async def login_page(request: Request):
        return templates.TemplateResponse(request=request, name="login.html", context={})

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        if (form.get("email") != settings.demo_email
                or form.get("password") != settings.demo_password):
            raise HTTPException(401, "invalid synthetic credentials")
        tenant = form.get("tenant", "")
        with db() as conn:
            environment(conn, tenant)
            token = secrets.token_urlsafe(32)
            conn.execute("INSERT INTO sessions VALUES(?,?,?)",
                         (token, tenant, secrets.token_urlsafe(24)))
        response = RedirectResponse("/customers", status_code=303)
        response.set_cookie("myelin_session", token, httponly=True, samesite="strict")
        return response

    @app.get("/customers")
    async def customers(request: Request):
        with db() as conn:
            s = session(request, conn)
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM customers WHERE tenant=? ORDER BY name", (s["tenant"],))]
        return templates.TemplateResponse(request=request, name="customers.html",
                                          context={"customers": rows})

    @app.get("/customers/{customer_id}/invoices/new")
    async def new_invoice(customer_id: str, request: Request):
        with db() as conn:
            s = csrf_form(request, conn)
            customer = scoped(conn, "customers", customer_id, s["tenant"])
            env = environment(conn, s["tenant"])
        return templates.TemplateResponse(request=request, name="new_invoice.html", context={
            "customer": customer, "csrf": s["csrf"], "operation_id": str(uuid4()),
            "renamed": "rename_field:invoice_total" in env["mutations"],
            "reordered": "reorder_fields:invoice" in env["mutations"],
        })

    @app.post("/customers/{customer_id}/invoices")
    async def create_invoice(customer_id: str, request: Request):
        form = await request.form()
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            s = session(request, conn)
            validate_csrf(s, form)
            scoped(conn, "customers", customer_id, s["tenant"])
            env = environment(conn, s["tenant"])
            renamed = "rename_field:invoice_total" in env["mutations"]
            key = "amount_due_cents" if renamed else "total_cents"
            obsolete = "total_cents" if renamed else "amount_due_cents"
            if obsolete in form or key not in form:
                raise HTTPException(422, f"request contract requires {key}")
            quantity = integer(form, "quantity", 1)
            unit = integer(form, "unit_price_cents")
            total = integer(form, key)
            if total != quantity * unit:
                raise HTTPException(422, "invoice total must equal quantity * unit price")
            try:
                due = str(form["due_date"])
                if date.fromisoformat(due).isoformat() != due:
                    raise ValueError("non-canonical date")
            except (KeyError, ValueError) as exc:
                raise HTTPException(422, "due_date must be an ISO date") from exc
            description = form.get("description")
            if not isinstance(description, str) or not description.strip():
                raise HTTPException(422, "description required")
            op = identity(request, form)
            payload = {"kind": "create_invoice", "customer_id": customer_id,
                       "description": description, "quantity": quantity,
                       "unit_price_cents": unit, "total_cents": total, "due_date": due}
            digest, result = replay(conn, s["tenant"], op, payload)
            if result is None:
                invoice_id = str(uuid4())
                conn.execute("INSERT INTO invoices VALUES(?,?,?,?,?,?,?,?,?)",
                             (invoice_id, s["tenant"], customer_id, description, quantity,
                              unit, total, due, "unpaid"))
                result = {"location": f"/invoices/{invoice_id}", "invoice_id": invoice_id}
                save_operation(conn, s["tenant"], op, digest, invoice_id, result)
        return RedirectResponse(result["location"], status_code=303)

    @app.get("/invoices/{invoice_id}")
    async def invoice_page(invoice_id: str, request: Request):
        with db() as conn:
            s = csrf_form(request, conn)
            invoice = scoped(conn, "invoices", invoice_id, s["tenant"])
            env = environment(conn, s["tenant"])
        return templates.TemplateResponse(request=request, name="invoice.html", context={
            "invoice": invoice, "csrf": s["csrf"], "operation_id": str(uuid4()),
            "moved": "move_button:mark_paid" in env["mutations"],
            "consent": "add_modal:consent" in env["mutations"],
        })

    @app.post("/invoices/{invoice_id}/pay")
    async def pay(invoice_id: str, request: Request):
        form = await request.form()
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            s = session(request, conn)
            validate_csrf(s, form)
            invoice = scoped(conn, "invoices", invoice_id, s["tenant"])
            op = identity(request, form)
            digest, result = replay(conn, s["tenant"], op,
                                    {"kind": "pay", "invoice_id": invoice_id})
            if result is None:
                if invoice["status"] == "paid":
                    raise HTTPException(409, "invoice already paid by another operation")
                conn.execute("INSERT INTO payments VALUES(?,?,?,?)",
                             (str(uuid4()), s["tenant"], invoice_id, invoice["total_cents"]))
                conn.execute("UPDATE invoices SET status='paid' WHERE id=? AND tenant=?",
                             (invoice_id, s["tenant"]))
                result = {"location": f"/invoices/{invoice_id}", "invoice_id": invoice_id}
                save_operation(conn, s["tenant"], op, digest, invoice_id, result)
        return RedirectResponse(result["location"], status_code=303)

    @app.get("/api/operations/{operation_id}")
    async def operation_status(operation_id: str, request: Request):
        with db() as conn:
            # Waits for writers: absent is definitive after an in-progress transaction finishes.
            conn.execute("BEGIN IMMEDIATE")
            s = session(request, conn)
            row = conn.execute("SELECT * FROM operations WHERE tenant=? AND operation_id=?",
                               (s["tenant"], operation_id)).fetchone()
        if row is None:
            return {"status": "absent", "request_hash": None, "entity_kind": None,
                    "entity_id": None, "result": None}
        return {"status": "applied", "request_hash": row["request_hash"],
                "entity_kind": row["entity_kind"], "entity_id": row["entity_id"],
                "result": json.loads(row["result"])}

    return app
