"""Live sidecars deliberately leave Program v1 serialization untouched."""

import hashlib
import ipaddress
import json
import re
from typing import Literal
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from pydantic import Field, field_validator, model_validator

from myelin.schema import AssertionResult, Contract, LocatorSpec, ValueRef


def identifier(value):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}", value) or ".." in value:
        raise ValueError("invalid registered identifier")
    return value


def digest(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=lambda item: item.model_dump(mode="json"),
        ).encode()
    ).hexdigest()


def https_origin(url):
    p = urlsplit(url)
    if (
        p.scheme != "https"
        or not p.hostname
        or p.username
        or p.password
        or p.port not in (None, 443)
    ):
        raise ValueError("live origins require HTTPS without credentials or custom ports")
    host = p.hostname.lower()
    if (
        host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
        or "." not in host
    ):
        raise ValueError("private/local hosts are not live sites")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("private/local addresses are not live sites")
    return f"https://{p.netloc.lower()}"


class Pagination(Contract):
    next_locator: LocatorSpec
    max_pages: int = Field(ge=1, le=50)


class EvidenceRecipe(Contract):
    id: str
    kind: Literal["ui", "http"] = "ui"
    navigation: ValueRef | None = None
    locator: LocatorSpec | None = None
    scope_locator: LocatorSpec | None = None
    target_value: ValueRef | None = None
    read: Literal["text", "value", "attribute", "url", "title", "rows", "json_path"] = "text"
    attribute_or_path: str | None = None
    collect_all: bool = False
    pagination: Pagination | None = None
    # Enumeration must explicitly describe why the selected collection is exhaustive.
    completeness: Literal["single", "all_rendered", "paginated"] = "single"
    select_match: dict[str, ValueRef] = Field(default_factory=dict)
    select_contains: dict[str, ValueRef] = Field(default_factory=dict)
    project: str | None = None
    max_items: int = Field(default=1000, ge=1, le=10000)
    take_first: bool = False
    output_variable: str | None = None
    output_index: int | None = None
    item_match: dict[str, ValueRef] = Field(default_factory=dict)
    item_project: str | None = None


class OutcomeAssertion(Contract):
    name: str
    recipe_id: str
    operator: Literal["equals", "contains", "count_equals", "unique", "date_equals"]
    expected: ValueRef
    required: bool = True


class BindingRule(Contract):
    target: str
    operation: Literal["copy", "concat", "list", "local_datetime", "lookup", "format_datetime"]
    values: list[ValueRef]


class RequestMatch(Contract):
    method: Literal["POST", "PUT", "PATCH", "DELETE"]
    origin: str
    path_pattern: str
    required_body: dict[str, ValueRef] = Field(default_factory=dict)
    body_templates: dict[str, str] = Field(default_factory=dict)
    body_contains: dict[str, list[ValueRef]] = Field(default_factory=dict)
    path_bindings: dict[str, ValueRef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded(self):
        if https_origin(self.origin) != self.origin:
            raise ValueError("request origin must be exact")
        if not self.path_pattern.startswith("/") or len(self.path_pattern) > 300:
            raise ValueError("request path pattern must be bounded and absolute")
        re.compile(self.path_pattern)
        return self


class EffectDefinition(Contract):
    key: str
    kind: Literal["create", "update"]
    target: ValueRef
    depends_on: list[str] = Field(default_factory=list)
    request: RequestMatch
    assertions: list[str] = Field(min_length=1)
    # Required on create: a declared input/derived marker in the first persisted request.
    initial_marker_field: str | None = None


class SiteProfile(Contract):
    id: str
    start_url: str
    navigation_origins: list[str] = Field(min_length=1)
    api_origins: list[str] = Field(default_factory=list)
    auth_origins: list[str] = Field(default_factory=list)
    asset_origins: list[str] = Field(default_factory=list)
    auth_profile_id: str
    scope_locator: dict[str, EvidenceRecipe]
    capabilities: list[str] = Field(default_factory=lambda: ["ui"])
    sensitive_keys: list[str] = Field(default_factory=list)
    supported_idempotency: Literal["none"] = "none"
    read_only_graphql_queries: list[str] = Field(default_factory=list)

    _ids = field_validator("id", "auth_profile_id")(identifier)

    @model_validator(mode="after")
    def origins(self):
        for query in self.read_only_graphql_queries:
            if not re.match(r"^\s*query\b", query) or re.search(
                r"\b(mutation|subscription)\b", query
            ):
                raise ValueError(
                    "only observed read-only GraphQL queries may bypass write ticketing"
                )
        for origin in (
            self.navigation_origins + self.api_origins + self.auth_origins + self.asset_origins
        ):
            if https_origin(origin) != origin:
                raise ValueError("origin grants must be exact origins")
        if https_origin(self.start_url) not in self.navigation_origins:
            raise ValueError("start URL outside navigation scope")
        if set(self.scope_locator) != {"account_id", "workspace_id", "resource_id"}:
            raise ValueError("account, workspace and resource identity recipes required")
        return self


class WorkflowSpec(Contract):
    id: str
    revision: int = Field(ge=1)
    site_profile_id: str
    goal: str = Field(min_length=1)
    input_schema: dict
    derived_fields: list[BindingRule] = Field(default_factory=list)
    task_key_field: str
    scope: dict[str, str]
    marker_template: str
    outcome_contract: list[OutcomeAssertion] = Field(min_length=1)
    evidence_recipes: list[EvidenceRecipe] = Field(min_length=1)
    compilation_policy: Literal["ui_first", "http_preferred"] = "ui_first"
    allowed_effects: list[str] = Field(default_factory=list)
    effect_contract: list[EffectDefinition] = Field(default_factory=list)
    max_new_records: int = Field(default=1, ge=0, le=1)

    _ids = field_validator("id", "site_profile_id")(identifier)

    @model_validator(mode="after")
    def consistent(self):
        Draft202012Validator.check_schema(self.input_schema)
        if set(self.scope) != {"account_id", "workspace_id", "resource_id"} or not all(
            self.scope.values()
        ):
            raise ValueError("complete expected account/workspace/resource scope required")
        if self.task_key_field not in self.input_schema.get("required", []):
            raise ValueError("task key must be a required input")
        recipes = {r.id for r in self.evidence_recipes}
        names = {a.name for a in self.outcome_contract}
        if len(recipes) != len(self.evidence_recipes) or len(names) != len(self.outcome_contract):
            raise ValueError("duplicate recipe/assertion")
        if not any(a.required for a in self.outcome_contract):
            raise ValueError("required outcome assertions cannot be empty")
        if any(a.recipe_id not in recipes for a in self.outcome_contract):
            raise ValueError("unknown outcome recipe")
        keys = [e.key for e in self.effect_contract]
        if len(set(keys)) != len(keys) or set(keys) != set(self.allowed_effects):
            raise ValueError("effect keys must exactly match allowed effects")
        seen = set()
        creates = 0
        for effect in self.effect_contract:
            if not set(effect.depends_on) <= seen or not set(effect.assertions) <= names:
                raise ValueError("invalid effect dependency or evidence assertion")
            seen.add(effect.key)
            if effect.kind == "create":
                creates += 1
                marker = effect.request.required_body.get(effect.initial_marker_field)
                if marker is None or marker.kind != "input":
                    raise ValueError("create requires a bound atomic initial marker")
        if creates and self.max_new_records == 0:
            raise ValueError("read-only workflow cannot create")
        return self


class ReadBinding(Contract):
    variable: str
    recipe_id: str


class LiveProgramBinding(Contract):
    candidate_hash: str
    spec_hash: str
    site_profile_hash: str
    account_id: str
    workspace_id: str
    resource_scope: str
    effect_bindings: dict[str, str]
    before_step_reads: dict[str, list[ReadBinding]] = Field(default_factory=dict)
    after_step_reads: dict[str, list[ReadBinding]] = Field(default_factory=dict)
    evidence_recipe_hash: str
    auth_profile_id: str
    http_evidence: dict[str, "HttpEvidence"] = Field(default_factory=dict)


class HttpEvidence(Contract):
    source_run: str
    request_id: str
    source_action_id: str
    request_hash: str
    cookie_bindings: dict[str, str] = Field(default_factory=dict)

    _run_id = field_validator("source_run")(identifier)


class EvidenceResult(Contract):
    status: Literal["verified", "failed", "inconclusive"]
    assertions: list[AssertionResult]
    resource_ids: list[str] = Field(default_factory=list)
    resource_urls: list[str] = Field(default_factory=list)
    captured_at: float
    scope_identity: dict[str, str]
    evidence_paths: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
