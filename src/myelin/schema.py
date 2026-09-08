"""Versioned persistence contracts. No live handles or secrets belong in these models."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LiteralRef(Contract):
    kind: Literal["literal"] = "literal"
    value: JsonValue


class NamedRef(Contract):
    kind: Literal["input", "variable", "secret"]
    key: str


class MultiplyRef(Contract):
    kind: Literal["multiply_int"] = "multiply_int"
    left: ValueRef
    right: ValueRef


ValueRef = Annotated[LiteralRef | NamedRef | MultiplyRef, Field(discriminator="kind")]


class LocatorSpec(Contract):
    strategy: Literal["role", "label", "text", "test_id"]
    value: str
    role: str | None = None
    exact: bool = True


class EnvironmentSpec(Contract):
    app: str
    revision: str
    mutations: list[str] = Field(default_factory=list)
    seed_version: str = "crm-seed-v1"


class CaseSpec(Contract):
    case_id: str
    inputs: dict[str, JsonValue]
    seed_version: str


class UsageRecord(Contract):
    response_id: str
    purpose: Literal["record", "compile", "reference", "repair", "steer"]
    model: str
    input_tokens: int = Field(ge=0)
    cached_tokens: int = Field(ge=0)
    cache_write_tokens: int | None = None
    output_tokens: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    usd: str | None = None
    pricing_revision: str | None = None


class Observation(Contract):
    id: str
    action_id: str | None = None
    timestamp: float
    url: str
    frame_path: str
    aria_path: str
    aria_hash: str
    business_state: JsonValue = None
    state_hash: str | None = None


class NetworkEvent(Contract):
    request_id: str
    action_id: str | None = None
    timestamp: float
    method: str
    url: str
    resource_type: str
    request_headers: dict[str, str]
    request_body: JsonValue = None
    response_status: int | None = None
    response_headers: dict[str, str] = Field(default_factory=dict)
    response_body: JsonValue = None
    body_omitted_reason: str | None = None
    redirect_from: str | None = None
    test_traffic: bool = False


class RecordedAction(Contract):
    id: str
    response_id: str
    call_id: str
    intent: str
    operation: Literal["navigate", "click", "fill", "select", "check", "submit", "inspect", "press"]
    target: LocatorSpec | None = None
    arguments: dict[str, ValueRef]
    before_id: str
    after_id: str
    request_ids: list[str]
    code: str | None = None
    outcome: Literal["success", "failure"]
    error: str | None = None


class Compare(Contract):
    kind: Literal["compare"] = "compare"
    source: Literal["input", "variable", "business", "response", "url", "aria"]
    path: str
    op: Literal["eq", "ne", "gt", "ge", "lt", "le", "contains", "exists"]
    expected: ValueRef


class Compound(Contract):
    kind: Literal["all", "any"]
    items: list[Predicate] = Field(min_length=1)


Predicate = Annotated[Compare | Compound, Field(discriminator="kind")]


class ExtractRule(Contract):
    target_var: str
    source: Literal["json_path", "html_input", "header", "cookie"]
    expression: str
    secret: bool = False


class StepCommon(Contract):
    id: str
    intent: str
    source_action_ids: list[str] = Field(min_length=1)
    pre: list[Predicate] = Field(min_length=1)
    post: list[Predicate] = Field(min_length=1)
    resume_url: ValueRef | None = None
    effect: Literal["read", "write"]
    operation_key: str | None = None
    depends_on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def write_identity(self):
        if self.effect == "write" and not self.operation_key:
            raise ValueError("write operations require a stable operation_key")
        return self


class HttpStep(StepCommon):
    kind: Literal["http"] = "http"
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]
    url: ValueRef
    path_params: dict[str, ValueRef] = Field(default_factory=dict)
    query: dict[str, ValueRef] = Field(default_factory=dict)
    headers: dict[str, ValueRef] = Field(default_factory=dict)
    body_kind: Literal["none", "json", "form"]
    body: dict[str, ValueRef] = Field(default_factory=dict)
    extract: list[ExtractRule] = Field(default_factory=list)


class UiStep(StepCommon):
    kind: Literal["ui"] = "ui"
    action: Literal["navigate", "click", "fill", "select", "check", "submit", "press"]
    target: LocatorSpec | None = None
    arguments: dict[str, ValueRef]


class Branch(StepCommon):
    kind: Literal["branch"] = "branch"
    condition: Predicate
    then: list[ProgramStep]
    otherwise: list[ProgramStep]
    rationale: str
    source_steer_id: str


ProgramStep = Annotated[HttpStep | UiStep | Branch, Field(discriminator="kind")]


class Program(Contract):
    schema_version: Literal[1] = 1
    workflow: str
    app: str
    version: int = Field(ge=1)
    parent_hash: str | None = None
    input_schema: dict[str, JsonValue]
    policy_revision: str
    supported_revisions: list[str] = Field(min_length=1)
    steps: list[ProgramStep] = Field(min_length=1)
    final_post: list[Predicate] = Field(min_length=1)
    compiled_from: list[str] = Field(min_length=1)
    notes: str = ""

    @model_validator(mode="after")
    def unique_ids(self):
        ids, keys = set(), set()

        def visit(steps):
            for step in steps:
                if step.id in ids:
                    raise ValueError(f"duplicate step ID: {step.id}")
                if step.operation_key and step.operation_key in keys:
                    raise ValueError(f"duplicate operation key: {step.operation_key}")
                ids.add(step.id)
                if step.operation_key:
                    keys.add(step.operation_key)
                if isinstance(step, Branch):
                    visit(step.then)
                    visit(step.otherwise)

        visit(self.steps)
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


class AssertionResult(Contract):
    name: str
    passed: bool
    expected: JsonValue
    observed: JsonValue


class Checkpoint(Contract):
    run_id: str
    step_id: str
    completed_step_ids: list[str]
    variables: dict[str, JsonValue]
    secret_refs: list[str]
    resume_url: str | None
    observation_id: str
    operation_id: str | None


class FailureContext(Contract):
    checkpoint: Checkpoint
    request_id: str | None
    error_kind: Literal["precondition", "transport", "status", "postcondition", "oracle"]
    effect_status: Literal["not_applied", "applied", "unknown"]
    expected: JsonValue
    observed: JsonValue
    remaining_goal: str


class RunResult(Contract):
    run_id: str
    success: bool
    status: Literal["completed", "failed", "budget_exhausted"]
    program_hash: str | None
    environment: EnvironmentSpec
    final_observation: str
    failed_step_id: str | None = None
    failure: FailureContext | None = None
    oracle_assertions: list[AssertionResult]
    model_calls: int = Field(ge=0)
    model_usd: str | None
    wall_ms: int = Field(ge=0)
    http_requests: int = Field(ge=0)
    ui_actions: int = Field(ge=0)
    work_units: int = Field(ge=0)


class HttpCandidate(Contract):
    id: str
    source_action_ids: list[str]
    request_ids: list[str]
    prerequisite_ids: list[str]
    step: HttpStep
    confidence: Literal["high", "medium", "low"]
    evidence: list[str]
    unresolved: list[str]


class GateRequest(Contract):
    candidate_hash: str
    workflow: str
    environment: EnvironmentSpec
    policy_revision: str
    suite_hash: str
    reference_mode: Literal["none", "sampled", "full"]
    reference_case_ids: list[str]


class CaseResult(Contract):
    case_id: str
    program_run_id: str
    program_success: bool
    assertions: list[AssertionResult]
    reference_run_id: str | None
    reference_status: Literal["not_requested", "pending", "success", "failure"]
    outcome_match: bool | None
    differences: list[AssertionResult]
    program_work_units: int
    program_wall_ms: int


class GateResult(Contract):
    gate_id: str
    request: GateRequest
    status: Literal["passed", "failed", "inconclusive"]
    cases: list[CaseResult]
    oracle_passes: int
    oracle_total: int
    reference_passes: int
    reference_total: int
    all_references_complete: bool
    model_usage: list[str]
    median_wall_ms: int
    max_work_units: int


class VersionRow(Contract):
    candidate_hash: str
    parent_hash: str | None
    version: int
    environment_revision: str
    policy_revision: str
    suite_hash: str
    gate_id: str
    mode: Literal["initial", "optimization", "restoration", "policy_change"]
    verdict: Literal["promoted", "rejected", "inconclusive"]
    metrics_before: dict[str, JsonValue] | None
    metrics_after: dict[str, JsonValue]
    rule: str
    reason: str
    timestamp: float


class Event(Contract):
    seq: int = Field(ge=1)
    timestamp: float
    run_id: str
    kind: str
    payload: dict[str, JsonValue]


class SteerMark(Contract):
    id: str
    response_id: str
    action_id: str | None
    text: str
    observation_id: str
    timestamp: float
    accepted: bool
    predicate: Predicate | None
    policy_revision: str | None


class Trace(Contract):
    schema_version: Literal[1] = 1
    run_id: str
    workflow: str
    inputs: dict[str, JsonValue]
    environment: EnvironmentSpec
    policy_revision: str
    initial_observation: str
    actions: list[RecordedAction]
    usage_response_ids: list[str]
    steer_marks: list[SteerMark]
    outcome: Literal["running", "awaiting_verification", "success", "failure", "aborted"]
    final_observation: str | None


for _model in (MultiplyRef, Compound, Branch, Program, Trace):
    _model.model_rebuild()
