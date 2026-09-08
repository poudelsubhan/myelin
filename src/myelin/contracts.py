"""Runtime interfaces frozen before engine consumers; unavailable is never success."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from myelin.schema import (
    AssertionResult,
    Checkpoint,
    EnvironmentSpec,
    GateRequest,
    GateResult,
    LocatorSpec,
    Program,
    RunResult,
    Trace,
    VersionRow,
)


class FeatureUnavailable(RuntimeError):
    pass


@dataclass
class RawObservation:
    url: str
    screenshot: bytes
    aria: str


class BrowserSession(Protocol):
    async def open(self, environment: EnvironmentSpec, tenant: str) -> None: ...
    async def perform(
        self,
        action_id: str,
        operation: str,
        target: LocatorSpec | None,
        resolved_arguments: dict,
        operation_id: str | None = None,
    ) -> Any: ...
    async def request(
        self,
        request_id: str,
        method: str,
        url: str,
        headers: dict,
        body: Any,
        operation_id: str | None = None,
    ) -> Any: ...
    async def snapshot(self) -> RawObservation: ...
    async def close(self) -> None: ...


class Recorder(Protocol):
    async def __call__(
        self,
        workflow: str,
        inputs: dict,
        environment: EnvironmentSpec,
        session: BrowserSession | None = None,
        checkpoint: Checkpoint | None = None,
        remaining_goal: str | None = None,
    ) -> Trace: ...


class Executor(Protocol):
    async def __call__(
        self,
        program: Program,
        inputs: dict,
        environment: EnvironmentSpec,
        session: BrowserSession | None = None,
    ) -> RunResult: ...


class Verifier(Protocol):
    def __call__(
        self, workflow: str, before_state: dict, after_state: dict, inputs: dict, policy: dict
    ) -> list[AssertionResult]: ...


async def unavailable(*args, **kwargs):
    raise FeatureUnavailable("Provider has not passed its integration gate")


def no_session() -> BrowserSession:
    raise FeatureUnavailable("Browser provider has not passed its integration gate")


@dataclass
class Services:
    recorder: Recorder = unavailable
    executor: Executor = unavailable
    compiler: Callable[..., Awaitable[Program]] = unavailable
    gate: Callable[[GateRequest], Awaitable[GateResult]] = unavailable
    promotion: Callable[..., Awaitable[VersionRow]] = unavailable
    session_factory: Callable[[], BrowserSession] = no_session


@dataclass
class ExecutionContext:
    session_handle: BrowserSession
    inputs: dict
    environment: EnvironmentSpec
    policy_revision: str
    variables: dict = field(default_factory=dict)
    secret_store: dict = field(default_factory=dict, repr=False)
    operation_ids: dict = field(default_factory=dict)
