"""Offline regressions for the live write boundary and immutable demo compatibility."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from myelin.console.app import create_app
from myelin.live.derived import derive
from myelin.live.effects import EffectBoundary
from myelin.live.journal import InputConflict, Journal, UnresolvedEffect
from myelin.live.learning import compile_ui, validate_live_program
from myelin.live.ledger import LiveLedger
from myelin.live.origins import OriginPolicy
from myelin.live.schema import EvidenceResult, SiteProfile, WorkflowSpec, digest
from myelin.live.verification import Verifier, compare
from myelin.runtime.registry import Registry
from myelin.schema import EnvironmentSpec, Program, RecordedAction, Trace
from myelin.trace.sanitize import Sanitizer


def literal(value):
    return {"kind": "literal", "value": value}


def inp(key):
    return {"kind": "input", "key": key}


@pytest.fixture
def site():
    return SiteProfile.model_validate(
        {
            "id": "example",
            "start_url": "https://example.com/board",
            "auth_profile_id": "account",
            "navigation_origins": ["https://example.com"],
            "api_origins": ["https://api.example.com"],
            "asset_origins": ["https://cdn.example.com"],
            "scope_locator": {
                key: {"id": key, "read": "text", "locator": {"strategy": "test_id", "value": key}}
                for key in ("account_id", "workspace_id", "resource_id")
            },
        }
    )


@pytest.fixture
def spec(site):
    return WorkflowSpec.model_validate(
        {
            "id": "sales",
            "revision": 1,
            "site_profile_id": site.id,
            "goal": "Create the requested lead",
            "input_schema": {
                "type": "object",
                "properties": {"task_key": {"type": "string"}, "name": {"type": "string"}},
                "required": ["task_key", "name"],
                "additionalProperties": False,
            },
            "derived_fields": [
                {
                    "target": "title",
                    "operation": "concat",
                    "values": [inp("name"), literal(" ["), inp("task_key"), literal("]")],
                }
            ],
            "task_key_field": "task_key",
            "scope": {"account_id": "a", "workspace_id": "w", "resource_id": "b"},
            "marker_template": "[{task_key}]",
            "evidence_recipes": [
                {
                    "id": "resource_urls",
                    "read": "attribute",
                    "attribute_or_path": "href",
                    "collect_all": True,
                    "completeness": "all_rendered",
                    "locator": {"strategy": "test_id", "value": "card"},
                },
                {
                    "id": "title",
                    "read": "text",
                    "locator": {"strategy": "test_id", "value": "title"},
                },
            ],
            "outcome_contract": [
                {
                    "name": "one",
                    "recipe_id": "resource_urls",
                    "operator": "unique",
                    "expected": literal(1),
                },
                {
                    "name": "title",
                    "recipe_id": "title",
                    "operator": "equals",
                    "expected": inp("title"),
                },
            ],
            "allowed_effects": ["create"],
            "effect_contract": [
                {
                    "key": "create",
                    "kind": "create",
                    "target": literal("b"),
                    "request": {
                        "origin": "https://example.com",
                        "method": "POST",
                        "path_pattern": "/cards",
                        "required_body": {"name": inp("title")},
                    },
                    "assertions": ["one", "title"],
                    "initial_marker_field": "name",
                }
            ],
        }
    )


def test_registry_arbitrary_origins_paths_and_immutable_specs(tmp_path, site, spec):
    registry = Registry(tmp_path)
    registry.put(site)
    registry.put(spec)
    assert registry.workflow("sales") == spec
    other = site.model_copy(
        update={
            "id": "another",
            "start_url": "https://python.org/",
            "navigation_origins": ["https://python.org"],
        }
    )
    registry.put(other)
    assert registry.site("another").start_url == "https://python.org/"
    for value in ("../secrets", "x/y", ".", "..", "a\\b"):
        with pytest.raises(ValueError):
            registry.site(value)
    with pytest.raises(ValueError):
        registry.put(spec.model_copy(update={"goal": "weaker"}))
    for url in (
        "http://example.com",
        "https://localhost",
        "https://127.0.0.1",
        "https://user:pw@example.com",
    ):
        with pytest.raises(ValueError):
            SiteProfile.model_validate(
                site.model_dump() | {"start_url": url, "navigation_origins": [url]}
            )
    assert (tmp_path / "sites/example.json").stat().st_mode & 0o777 == 0o600


def test_origin_grants_separate_assets_from_writes(site):
    policy = OriginPolicy(site)
    assert policy.permit("GET", "https://cdn.example.com/style.css", "stylesheet")
    with pytest.raises(ValueError):
        policy.permit("POST", "https://cdn.example.com/cards", "fetch")
    with pytest.raises(ValueError):
        policy.navigation("https://api.example.com/")
    with pytest.raises(ValueError):
        policy.permit("POST", "https://example.com/__reset", "fetch")


def test_derived_dates_quotes_and_input_conflict(tmp_path, spec):
    raw = {"task_key": "a", "name": 'Quotes " and unicode —'}
    values = derive(spec, raw)
    assert values["title"] == 'Quotes " and unicode — [a]'
    j = Journal(tmp_path / "journal.sqlite")
    assert j.preflight("scope", "a", values) is None
    with pytest.raises(InputConflict):
        j.preflight("scope", "a", values | {"name": "changed"})
    assert compare("date_equals", "2026-09-09T16:00:00Z", "2026-09-09T09:00:00-07:00")
    assert not compare("date_equals", "2026-09-09T09:00:00Z", "2026-09-09T09:00:00-07:00")


def test_journal_restart_never_repeats_dispatched_write(tmp_path):
    path = tmp_path / "journal.sqlite"
    j = Journal(path)
    data = {"task_key": "a"}
    j.preflight("s", "a", data)
    assert j.prepare("s", "a", "create", data)
    j.dispatch("s", "a", "create", {"name": "a"})
    j2 = Journal(path)
    j2.preflight("s", "a", data)
    assert j2.rows("s", "a")[0]["state"] == "unknown"
    for key in ("create", "renamed-action"):
        with pytest.raises(UnresolvedEffect):
            j2.prepare("s", "a", key, data)
    with pytest.raises(ValueError):
        j2.finish("s", "a", "create", "not_applied", {"status": 422})
    j2.finish("s", "a", "create", "verified", {"resource_urls": ["https://example.com/c/1"]})
    assert not j2.prepare("s", "a", "create", data)
    with pytest.raises(ValueError):
        j2.finish("s", "a", "create", "unknown", {})
    with j.lease("account"), pytest.raises(UnresolvedEffect), j2.lease("account"):
        pass


async def emit(*args):
    pass


class FakeVerifier:
    status = "verified"

    async def verify(self, *args):
        return EvidenceResult(
            status=self.status,
            assertions=[],
            scope_identity={},
            captured_at=time.time(),
            resource_urls=["https://example.com/c/1"],
        )


async def test_effect_key_precedes_dispatch_and_survives_compilation(tmp_path, spec):
    inputs = derive(spec, {"task_key": "a", "name": "New"})
    journal = Journal(tmp_path / "j.sqlite")
    journal.preflight("s", "a", inputs)
    verifier = FakeVerifier()
    boundary = EffectBoundary(journal, spec, inputs, "s", verifier, emit)
    session = SimpleNamespace(variables={}, secrets={})
    assert await boundary.begin("model-action-1", "create")
    assert journal.rows("s", "a")[0]["state"] == "prepared"
    await boundary.authorize_request(
        session, "POST", "https://example.com/cards", {"name": "New [a]"}
    )
    assert journal.rows("s", "a")[0]["state"] == "dispatched"
    await boundary.finish(session)
    assert not await boundary.begin("compiled-step-different-id", "create")
    assert boundary.action_bindings == {
        "model-action-1": "create",
        "compiled-step-different-id": "create",
    }


async def test_unmapped_wrong_payload_and_unknown_write_fail_closed(tmp_path, spec):
    inputs = derive(spec, {"task_key": "a", "name": "New"})
    session = SimpleNamespace(variables={}, secrets={})
    for case in ("unmapped", "wrong_payload", "lost_response"):
        j = Journal(tmp_path / (case + ".sqlite"))
        j.preflight("s", "a", inputs)
        v = FakeVerifier()
        b = EffectBoundary(j, spec, inputs, "s", v, emit)
        if case == "unmapped":
            with pytest.raises(UnresolvedEffect):
                await b.authorize_request(session, "POST", "https://example.com/cards", {})
            assert j.rows("s", "a") == []
        elif case == "wrong_payload":
            await b.begin("action", "create")
            with pytest.raises(UnresolvedEffect):
                await b.authorize_request(
                    session, "POST", "https://example.com/cards", {"name": "wrong"}
                )
            assert j.rows("s", "a")[0]["state"] == "prepared"
        else:
            await b.begin("action", "create")
            await b.authorize_request(
                session, "POST", "https://example.com/cards", {"name": "New [a]"}
            )
            v.status = "inconclusive"
            with pytest.raises(UnresolvedEffect):
                await b.finish(session, ConnectionError())
            assert j.rows("s", "a")[0]["state"] == "unknown"
            with pytest.raises(UnresolvedEffect):
                await b.begin("retry", "create")
            # A fresh owner can recover by exact independent read-back, not status codes.
            recovered = EffectBoundary(j, spec, inputs, "s", FakeVerifier(), emit)
            await recovered.reconcile(session)
            assert not await recovered.begin("resumed", "create")


async def test_verification_duplicates_scope_and_incomplete_pagination(site, spec):
    values = derive(spec, {"task_key": "a", "name": "New"})
    state = {"resource_urls": ["https://example.com/c/1"], "title": values["title"], **spec.scope}
    complete = True

    async def reader(session, recipe, inputs):
        return {"value": state[recipe.id], "complete": complete}

    session = SimpleNamespace(site=site, variables={}, secrets={})
    verifier = Verifier(reader, attempts=1)
    assert (await verifier.verify(session, spec, values)).status == "verified"
    state["resource_urls"] *= 2
    assert (await verifier.verify(session, spec, values)).status == "failed"
    state["resource_urls"] = state["resource_urls"][:1]
    complete = False
    assert (await verifier.verify(session, spec, values)).status == "inconclusive"
    complete = True
    state["workspace_id"] = "wrong"
    assert (await verifier.verify(session, spec, values)).status == "inconclusive"


def recorded(spec, site):
    inputs = derive(spec, {"task_key": "a", "name": "New"})
    action = RecordedAction(
        id="a1",
        response_id="r1",
        call_id="c1",
        intent="Create card",
        operation="click",
        target={"strategy": "role", "role": "button", "value": "Create"},
        arguments={},
        before_id="o1",
        after_id="o2",
        request_ids=["q1"],
        outcome="success",
    )
    trace = Trace(
        run_id="learn",
        workflow=spec.id,
        inputs=inputs,
        environment=EnvironmentSpec(
            app=site.id, revision=digest(site), seed_version="not-applicable"
        ),
        policy_revision=digest(spec),
        initial_observation="o1",
        actions=[action],
        usage_response_ids=["r1"],
        steer_marks=[],
        outcome="success",
        final_observation="o2",
    )
    return trace


def test_ui_only_compilation_provenance_and_bounded_canary_promotion(tmp_path, site, spec):
    trace = recorded(spec, site)
    program, binding = compile_ui(trace, spec, site, {"a1": "create"})
    assert program.steps[0].kind == "ui"
    with pytest.raises(ValueError):
        validate_live_program(
            program, binding.model_copy(update={"effect_bindings": {}}), spec, site, trace.inputs
        )
    j = Journal(tmp_path / "j.sqlite")
    ledger = LiveLedger(tmp_path / "ledger", j)
    h = ledger.put(program, binding, "a")
    scope = ledger.scope(spec, site)
    row = {
        "status": "verified",
        "candidate_hash": h,
        "scope_hash": scope,
        "spec_hash": digest(spec),
        "model_calls": 0,
        "reused": False,
    }
    with pytest.raises(ValueError):
        ledger.promote(scope, h, [row | {"task_key": "a"}, row | {"task_key": "c"}], None)
    with pytest.raises(ValueError):
        ledger.promote(scope, h, [row | {"task_key": "b"}, row | {"task_key": "b"}], None)
    ledger.promote(scope, h, [row | {"task_key": "b"}, row | {"task_key": "c"}], None)
    assert ledger.current(scope) == h
    with pytest.raises(ValueError):
        ledger.promote(scope, h, [row | {"task_key": "d"}, row | {"task_key": "e"}], None)
    assert ledger.current("other-scope") is None


def test_live_routes_private_no_demo_admin_or_reference(monkeypatch, tmp_path):
    monkeypatch.setenv("MYELIN_RUNS_DIR", str(tmp_path / "runs"))
    with TestClient(create_app()) as client:
        caps = client.get("/live/capabilities").json()
        assert "two_canary_gate" in caps["implemented"]
        assert client.get("/live", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.get("/live/registry", headers={"Host": "evil.example"}).status_code == 403
        assert client.post("/live/workflows/propose").status_code == 501
        assert client.get("/live").status_code == 200
        assert "Open demos" in client.get("/live").text
    root = Path(__file__).parents[1] / "src/myelin/live"
    for path in root.glob("*.py"):
        source = path.read_text()
        assert "myelin.verification.admin" not in source
        assert "GateRuntime" not in source and "reference_case(" not in source
        assert "X-Myelin-Operation-ID" not in source


def test_original_program_hashes_and_private_url_sanitization():
    for path in (Path(__file__).parents[1] / "programs").glob("*/*/program.json"):
        assert Program.model_validate_json(path.read_text()).content_hash() == path.parent.name
    sanitizer = Sanitizer()
    sanitizer.sensitive_keys.add("session_secret")
    clean = sanitizer.clean(
        {"url": "https://example.com/?token=abc&dsc=xyz", "session_secret": "hidden"}
    )
    assert "abc" not in json.dumps(clean) and "xyz" not in json.dumps(clean)
    assert "hidden" not in json.dumps(clean)


def test_graphql_transport_formatting_preserves_quoted_values():
    from myelin.browser.session import graphql_signature

    compact = "query Board($id:ID!){board(id:$id){name}}"
    formatted = "query Board ($id: ID!) { board(id: $id) { name } }"
    assert graphql_signature(compact) == graphql_signature(formatted)
    assert graphql_signature('query {x(value:"a b")}') != graphql_signature('query{x(value:"ab")}')
    assert graphql_signature("query{x}") != graphql_signature("mutation{x}")


def test_sensitive_field_names_do_not_destroy_keyboard_or_binding_schema():
    sanitizer = Sanitizer()
    sanitizer.sensitive_keys.add("key")
    assert sanitizer.clean({"key": literal("Enter")}) == {"key": literal("Enter")}
    assert sanitizer.clean({"key": inp("task_key")}) == {"key": inp("task_key")}
    assert sanitizer.clean({"key": "private-api-token"})["key"].startswith("<secret:")
    assert (
        sanitizer.clean({"password": literal("private-password")})["password"] == "<secret:empty>"
    )


@pytest.mark.asyncio
async def test_http_recipe_uses_current_cookie_and_rejects_unexplained_fields(tmp_path, site, spec):
    from myelin.live.http_recipes import prepare
    from myelin.live.schema import HttpEvidence
    from myelin.schema import HttpStep
    from myelin.trace.store import TraceStore

    event = {
        "request_id": "q",
        "action_id": "a",
        "method": "POST",
        "url": "https://api.example.com/cards",
        "request_body": {"name": "original", "dsc": "<secret:dsc>"},
    }
    folder = tmp_path / "runs" / "source"
    folder.mkdir(parents=True)
    (folder / "network.jsonl").write_text(json.dumps(event) + "\n")
    evidence = HttpEvidence(
        source_run="source",
        request_id="q",
        source_action_id="a",
        request_hash=digest(event),
        cookie_bindings={"dsc": "dsc"},
    )
    step = HttpStep(
        id="s",
        intent="Create",
        source_action_ids=["a"],
        pre=[
            {
                "kind": "compare",
                "source": "input",
                "path": "name",
                "op": "exists",
                "expected": literal(True),
            }
        ],
        post=[
            {
                "kind": "compare",
                "source": "input",
                "path": "name",
                "op": "exists",
                "expected": literal(True),
            }
        ],
        effect="write",
        operation_key="create",
        method="POST",
        url=literal(event["url"]),
        body_kind="json",
        body={"name": inp("name"), "dsc": {"kind": "secret", "key": "dsc"}},
    )

    class Context:
        async def cookies(self, url):
            assert url == event["url"]
            return [{"name": "dsc", "value": "current-token"}]

    session = SimpleNamespace(
        store=TraceStore(tmp_path / "runs", "now"),
        context=Context(),
        variables={},
        secrets={},
        origin_policy=OriginPolicy(site),
    )
    await prepare(session, step, evidence, {"name": "fresh"})
    assert session.secrets["dsc"] == "current-token"
    changed = step.model_copy(deep=True)
    changed.body["extra"] = literal("unobserved")
    with pytest.raises(ValueError, match="unexplained"):
        await prepare(session, changed, evidence, {"name": "fresh"})
    changed = step.model_copy(deep=True)
    changed.url = type(step.url)(value="https://other.example.com/cards")
    with pytest.raises(ValueError, match="origin"):
        await prepare(session, changed, evidence, {"name": "fresh"})
    evidence.cookie_bindings = {"dsc": "stale-token"}
    with pytest.raises(ValueError, match="credential source"):
        await prepare(session, step, evidence, {"name": "fresh"})


def test_csv_is_bounded_exact_and_preserves_quoted_briefs():
    from myelin.live.batch import parse_csv

    assert parse_csv('task_key,brief\na,"hello, world"\n', ["task_key", "brief"]) == [
        {"task_key": "a", "brief": "hello, world"}
    ]
    for text in [
        "task_key,task_key\na,b",
        "task_key,extra\na,b",
        "task_key,brief\na",
        "task_key,brief\n" + "a,b\n" * 6,
    ]:
        with pytest.raises(ValueError):
            parse_csv(text, ["task_key", "brief"])
