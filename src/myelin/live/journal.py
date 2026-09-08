"""SQLite intent-before-dispatch journal; task identity survives model/run/step changes."""

import fcntl
import json
import sqlite3
import time
from contextlib import contextmanager

from myelin.live.schema import digest
from myelin.runtime.registry import private_directory


class UnresolvedEffect(RuntimeError):
    pass


class InputConflict(ValueError):
    pass


class Journal:
    def __init__(self, path):
        self.path = path
        private_directory(path.parent)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    scope_hash TEXT, task_key TEXT, input_hash TEXT NOT NULL,
                    result_json TEXT, PRIMARY KEY(scope_hash, task_key));
                CREATE TABLE IF NOT EXISTS operations (
                    scope_hash TEXT, task_key TEXT, operation_key TEXT,
                    input_hash TEXT NOT NULL, state TEXT NOT NULL,
                    request_fingerprint TEXT, resource_ids_json TEXT DEFAULT '[]',
                    resource_urls_json TEXT DEFAULT '[]', evidence_json TEXT DEFAULT '{}',
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(scope_hash, task_key, operation_key));
            """)
        path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def lease(self, scope):
        # OS advisory lock survives await and is released on process death. SQLite
        # transactions additionally serialize all state transitions across workers.
        folder = private_directory(self.path.parent / "locks")
        with (folder / (digest(scope) + ".lock")).open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise UnresolvedEffect("account/profile already in use") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def preflight(self, scope, task, inputs):
        hashed = digest(inputs)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM tasks WHERE scope_hash=? AND task_key=?", (scope, task)
            ).fetchone()
            if row and row["input_hash"] != hashed:
                raise InputConflict("task key already belongs to different inputs")
            db.execute("INSERT OR IGNORE INTO tasks VALUES (?,?,?,NULL)", (scope, task, hashed))
            # A previous owner cannot still dispatch while our account lease is held.
            db.execute(
                "UPDATE operations SET state='unknown' WHERE scope_hash=? AND task_key=? "
                "AND state='dispatched'",
                (scope, task),
            )
            return json.loads(row["result_json"]) if row and row["result_json"] else None

    def rows(self, scope, task):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM operations WHERE scope_hash=? AND task_key=? "
                    "ORDER BY updated_at",
                    (scope, task),
                )
            ]

    def prepare(self, scope, task, key, inputs, dependencies=()):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task_row = db.execute(
                "SELECT input_hash FROM tasks WHERE scope_hash=? AND task_key=?", (scope, task)
            ).fetchone()
            if not task_row or task_row[0] != digest(inputs):
                raise InputConflict("whole-task preflight required")
            rows = {
                r["operation_key"]: dict(r)
                for r in db.execute(
                    "SELECT * FROM operations WHERE scope_hash=? AND task_key=?", (scope, task)
                )
            }
            if any(r["state"] in ("dispatched", "unknown") for r in rows.values()):
                raise UnresolvedEffect("reconcile every uncertain effect before more writes")
            if any(rows.get(dep, {}).get("state") != "verified" for dep in dependencies):
                raise UnresolvedEffect("effect dependencies not verified")
            old = rows.get(key)
            if old and old["state"] == "verified":
                return False
            db.execute(
                "INSERT INTO operations (scope_hash,task_key,operation_key,input_hash,state,"
                "updated_at) VALUES (?,?,?,?,'prepared',?) ON CONFLICT DO UPDATE SET "
                "state='prepared',updated_at=excluded.updated_at",
                (scope, task, key, digest(inputs), time.time()),
            )
            return True

    def dispatch(self, scope, task, key, request):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute(
                "UPDATE operations SET state='dispatched',request_fingerprint=?,"
                "updated_at=? WHERE scope_hash=? AND task_key=? AND operation_key=? "
                "AND state='prepared'",
                (digest(request), time.time(), scope, task, key),
            ).rowcount
            if updated != 1:
                raise UnresolvedEffect("effect already dispatched or not prepared")

    def finish(self, scope, task, key, state, evidence):
        if state not in ("verified", "unknown", "not_applied"):
            raise ValueError("invalid outcome state")
        if state != "unknown" and not evidence:
            raise ValueError("evidence required for definitive effect result")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM operations WHERE scope_hash=? AND task_key=? "
                "AND operation_key=?",
                (scope, task, key),
            ).fetchone()
            if not row or row[0] == "verified":
                raise ValueError("cannot invent or downgrade a verified effect")
            if state == "not_applied" and not evidence.get("definitely_not_applied"):
                raise ValueError("absence in a search is not proof of no effect")
            db.execute(
                "UPDATE operations SET state=?, evidence_json=?,resource_ids_json=?,"
                "resource_urls_json=?,updated_at=? WHERE scope_hash=? AND task_key=? "
                "AND operation_key=?",
                (
                    state,
                    json.dumps(evidence),
                    json.dumps(evidence.get("resource_ids", [])),
                    json.dumps(evidence.get("resource_urls", [])),
                    time.time(),
                    scope,
                    task,
                    key,
                ),
            )

    def complete(self, scope, task, evidence):
        if evidence["status"] != "verified":
            raise ValueError("only independently verified tasks can complete")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM operations WHERE scope_hash=? AND task_key=? "
                "AND state IN ('unknown','dispatched','prepared')",
                (scope, task),
            ).fetchone():
                raise UnresolvedEffect("cannot complete a task with unresolved effects")
            db.execute(
                "UPDATE tasks SET result_json=? WHERE scope_hash=? AND task_key=?",
                (json.dumps(evidence), scope, task),
            )
