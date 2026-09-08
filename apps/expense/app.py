"""Owned JSON expense application; its private oracle is outside the model tools."""

import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from apps.crm.app import Chaos, Reset
from myelin.adapters.expense import MUTATIONS, revision
from myelin.config import ROOT, Settings


def create_app(db_path: Path | None = None, settings: Settings | None = None):
    settings = settings or Settings.load()
    db_path = db_path or ROOT / "apps/expense/data/expense.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="Myelin Expenses")

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
        CREATE TABLE IF NOT EXISTS tenants(
          tenant TEXT PRIMARY KEY, environment TEXT, throttled INT);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, tenant TEXT);
        CREATE TABLE IF NOT EXISTS expenses(id TEXT PRIMARY KEY, tenant TEXT, merchant TEXT,
          amount_cents INTEGER, category TEXT, date TEXT, receipt_text TEXT, status TEXT,
          confirmation_token TEXT);
        CREATE TABLE IF NOT EXISTS submissions(id TEXT PRIMARY KEY, tenant TEXT,
          expense_id TEXT UNIQUE, manager_note TEXT);
        CREATE TABLE IF NOT EXISTS operations(tenant TEXT, operation_id TEXT, request_hash TEXT,
          entity_id TEXT, result TEXT, PRIMARY KEY(tenant, operation_id));
        """)

    def admin(request):
        if not settings.demo_token or not hmac.compare_digest(
            request.headers.get("X-Myelin-Demo-Token", ""), settings.demo_token
        ):
            raise HTTPException(403, "internal demo token required")

    def environment(conn, tenant):
        row = conn.execute("SELECT environment FROM tenants WHERE tenant=?", (tenant,)).fetchone()
        if row is None:
            raise HTTPException(404, "unknown tenant")
        return json.loads(row[0])

    def auth(request, conn):
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        row = conn.execute("SELECT tenant FROM sessions WHERE token=?", (token,)).fetchone()
        if row is None:
            raise HTTPException(401, "sign in required")
        return row[0]

    def scoped(conn, tenant, expense_id):
        row = conn.execute(
            "SELECT * FROM expenses WHERE tenant=? AND id=?", (tenant, expense_id)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "expense not found")
        return dict(row)

    def public(row, env):
        result = {k: v for k, v in dict(row).items() if k not in ("tenant", "confirmation_token")}
        result["resume_url"] = "/#" + result["id"]
        if "add_step:confirm_submit" in env["mutations"]:
            result["confirmation_token"] = row["confirmation_token"]
        return result

    def identity(request, payload, conn, tenant):
        op = request.headers.get("X-Myelin-Operation-ID", "")
        if not 1 <= len(op) <= 200:
            raise HTTPException(422, "operation identity required")
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        row = conn.execute(
            "SELECT * FROM operations WHERE tenant=? AND operation_id=?", (tenant, op)
        ).fetchone()
        if row and row["request_hash"] != digest:
            raise HTTPException(409, "operation identity conflict")
        return op, digest, json.loads(row["result"]) if row else None

    def save(conn, tenant, op, digest, expense_id, result):
        conn.execute(
            "INSERT INTO operations VALUES(?,?,?,?,?)",
            (tenant, op, digest, expense_id, json.dumps(result)),
        )

    @app.get("/health")
    async def health():
        return {"status": "ok", "app": "expense"}

    @app.post("/__reset")
    async def reset(body: Reset, request: Request):
        admin(request)
        env = body.environment
        if (
            body.seed_version != "expense-seed-v1"
            or env.seed_version != body.seed_version
            or env.app != "expense"
            or set(env.mutations) - MUTATIONS
            or env.revision != revision(env.mutations)
        ):
            raise HTTPException(422, "unsupported seed/environment/revision")
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for table in ("operations", "submissions", "expenses", "sessions", "tenants"):
                conn.execute(f"DELETE FROM {table} WHERE tenant=?", (body.tenant,))
            conn.execute("INSERT INTO tenants VALUES(?,?,0)", (body.tenant, env.model_dump_json()))
        return {"tenant": body.tenant, "environment": env}

    @app.get("/__state")
    async def state(request: Request, tenant: str):
        admin(request)
        with db() as conn:
            result = {"environment": environment(conn, tenant)}
            for table in ("expenses", "submissions"):
                result[table] = [
                    {k: v for k, v in dict(r).items() if k != "confirmation_token"}
                    for r in conn.execute(
                        f"SELECT * FROM {table} WHERE tenant=? ORDER BY id", (tenant,)
                    )
                ]
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
            mutations.add(body.mutation) if body.on else mutations.discard(body.mutation)
            env.update(mutations=sorted(mutations), revision=revision(mutations))
            conn.execute(
                "UPDATE tenants SET environment=?, throttled=0 WHERE tenant=?",
                (json.dumps(env), body.tenant),
            )
        return env

    @app.post("/api/login")
    async def login(request: Request):
        body = await request.json()
        if (
            body.get("email") != settings.demo_email
            or body.get("password") != settings.demo_password
        ):
            raise HTTPException(401, "invalid synthetic credentials")
        tenant = body.get("tenant", "")
        with db() as conn:
            environment(conn, tenant)
            token = secrets.token_urlsafe(32)
            conn.execute("INSERT INTO sessions VALUES(?,?)", (token, tenant))
        return {"token": token}

    @app.get("/api/expenses")
    async def expenses(request: Request):
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            tenant = auth(request, conn)
            env = environment(conn, tenant)
            done = conn.execute(
                "SELECT throttled FROM tenants WHERE tenant=?", (tenant,)
            ).fetchone()[0]
            if "throttle:list" in env["mutations"] and not done:
                conn.execute("UPDATE tenants SET throttled=1 WHERE tenant=?", (tenant,))
                return JSONResponse({"detail": "retry list"}, 429, headers={"Retry-After": "0.1"})
            rows = conn.execute("SELECT * FROM expenses WHERE tenant=? ORDER BY id", (tenant,))
            return {
                "expenses": [public(r, env) for r in rows],
                "amount_field": "cost_cents"
                if "rename_field:amount" in env["mutations"]
                else "amount_cents",
            }

    @app.post("/api/expenses")
    async def create(request: Request):
        body = await request.json()
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            tenant = auth(request, conn)
            env = environment(conn, tenant)
            key = "cost_cents" if "rename_field:amount" in env["mutations"] else "amount_cents"
            required = {"merchant", key, "category", "date", "receipt_text"}
            if (
                set(body) != required
                or type(body.get(key)) is not int
                or not 0 <= body[key] <= 2**53 - 1
            ):
                raise HTTPException(
                    422, f"requires {key} as integer cents and declared fields only"
                )
            for field in required - {key}:
                if not isinstance(body[field], str) or not body[field].strip():
                    raise HTTPException(422, f"{field} required")
            try:
                if date.fromisoformat(body["date"]).isoformat() != body["date"]:
                    raise ValueError("noncanonical date")
            except ValueError as exc:
                raise HTTPException(422, "date must be ISO date") from exc
            op, digest, result = identity(request, {"kind": "create", **body}, conn, tenant)
            if result is None:
                eid = str(uuid4())
                conn.execute(
                    "INSERT INTO expenses VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        eid,
                        tenant,
                        body["merchant"],
                        body[key],
                        body["category"],
                        body["date"],
                        body["receipt_text"],
                        "draft",
                        secrets.token_urlsafe(24),
                    ),
                )
                result = public(scoped(conn, tenant, eid), env)
                save(conn, tenant, op, digest, eid, result)
            return result

    @app.get("/api/expenses/{expense_id}")
    async def expense(expense_id: str, request: Request):
        with db() as conn:
            tenant = auth(request, conn)
            return public(scoped(conn, tenant, expense_id), environment(conn, tenant))

    @app.post("/api/expenses/{expense_id}/submit")
    async def submit(expense_id: str, request: Request):
        body = await request.json()
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            tenant = auth(request, conn)
            row = scoped(conn, tenant, expense_id)
            env = environment(conn, tenant)
            if set(body) - {"manager_note", "confirmation_token"} or not isinstance(
                body.get("manager_note", ""), str
            ):
                raise HTTPException(422, "invalid submission")
            if (
                "add_step:confirm_submit" in env["mutations"]
                and body.get("confirmation_token") != row["confirmation_token"]
            ):
                raise HTTPException(422, "confirmation_token required before submission")
            op, digest, result = identity(
                request, {"kind": "submit", "expense_id": expense_id, **body}, conn, tenant
            )
            if result is None:
                if row["status"] != "draft":
                    raise HTTPException(409, "already submitted by another operation")
                conn.execute(
                    "INSERT INTO submissions VALUES(?,?,?,?)",
                    (str(uuid4()), tenant, expense_id, body.get("manager_note", "")),
                )
                conn.execute(
                    "UPDATE expenses SET status='submitted' WHERE tenant=? AND id=?",
                    (tenant, expense_id),
                )
                result = {"id": expense_id, "status": "submitted"}
                save(conn, tenant, op, digest, expense_id, result)
            return result

    @app.get("/api/operations/{operation_id}")
    async def operation(operation_id: str, request: Request):
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            tenant = auth(request, conn)
            row = conn.execute(
                "SELECT * FROM operations WHERE tenant=? AND operation_id=?", (tenant, operation_id)
            ).fetchone()
        return {
            "status": "applied" if row else "absent",
            "request_hash": row["request_hash"] if row else None,
            "entity_kind": "expense" if row else None,
            "entity_id": row["entity_id"] if row else None,
            "result": json.loads(row["result"]) if row else None,
        }

    @app.get("/{path:path}")
    async def spa(path: str):
        return FileResponse(Path(__file__).parent / "static/index.html")

    return app
