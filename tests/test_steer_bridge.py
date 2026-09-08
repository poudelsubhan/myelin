from dataclasses import replace
from types import SimpleNamespace

import pytest

from myelin.astra.ws import SteeringState, SteeringUnresolved, WebSocketAstra
from myelin.config import Settings
from myelin.contracts import RawObservation
from myelin.steer.policy import TEXT, parse
from myelin.trace.store import TraceStore


class Event(SimpleNamespace):
    def model_dump(self, **kwargs):
        def dump(v):
            if isinstance(v, SimpleNamespace):
                return {k: dump(x) for k, x in vars(v).items()}
            if isinstance(v, list):
                return [dump(x) for x in v]
            return v

        return dump(self)


def response(kind, rid):
    return Event(
        type=kind,
        response=Event(
            id=rid, status="completed", output=[], incomplete_details=Event(reason="steered")
        ),
    )


async def setup(tmp_path, sequence):
    store = TraceStore(tmp_path, "socket")
    sent, accounted = [], []

    class Connection:
        def __init__(self):
            self.response = self
            self.sequence = iter(sequence)

        async def create(self, **kwargs):
            sent.append(("create", kwargs))

        async def steer(self, **kwargs):
            sent.append(("steer", kwargs))

        async def recv(self):
            return next(self.sequence)

    async def snapshot():
        return RawObservation("http://localhost:8102/", b"fixture", "heading: Expenses")

    async def emit(*args):
        pass

    bridge = WebSocketAstra(
        replace(Settings.load(), live=True, api_key="fixture"),
        store,
        emit,
        SimpleNamespace(action_id="real-action-position", snapshot=snapshot),
        TEXT,
    )
    bridge.connection = Connection()

    async def account(r, purpose):
        accounted.append(r.id)

    bridge.account = account
    return bridge, sent, accounted


def accepted():
    return Event(
        type="response.steer.accepted", steer=Event(id="server-steer", previous_response_id="r1")
    )


async def test_accepted_steer_waits_for_automatic_successor_without_extra_create(tmp_path):
    bridge, sent, accounted = await setup(
        tmp_path,
        [
            response("response.created", "r1"),
            accepted(),
            response("response.incomplete", "r1"),
            response("response.created", "r2"),
            response("response.completed", "r2"),
        ],
    )
    result = await bridge.respond("record", input=[])
    assert result.id == "r2"
    assert [kind for kind, _ in sent] == ["create", "steer"]
    assert sent[1][1] == {"previous_response_id": "r1", "input": TEXT}
    assert accounted == ["r1", "r2"]
    assert bridge.marks[0].id == "server-steer" and bridge.marks[0].predicate == parse(TEXT)
    assert bridge.marks[0].action_id == "real-action-position"


async def test_pending_delivers_original_tool_result_once_without_resending_text(tmp_path):
    pending = Event(
        type="response.steer.pending",
        steer=Event(id="server-steer", previous_response_id="r1"),
        required_input=[{"type": "function_call_output", "call_id": "call1"}],
    )
    bridge, sent, accounted = await setup(
        tmp_path,
        [
            response("response.created", "r1"),
            accepted(),
            response("response.completed", "r1"),
            pending,
            response("response.created", "r2"),
            response("response.completed", "r2"),
        ],
    )
    assert (await bridge.respond("record", input=[])).id == "r1"
    output = {"type": "function_call_output", "call_id": "call1", "output": "actual saved result"}
    assert (await bridge.respond("record", previous_response_id="r1", input=[output])).id == "r2"
    assert [k for k, _ in sent] == ["create", "steer", "create"]
    assert sent[-1][1]["input"] == [output]
    with pytest.raises(ValueError, match="duplicate"):
        await bridge.respond("record", input=[output])


def test_disconnect_reconciliation_and_only_unaccepted_instruction_recovery():
    state = SteeringState()
    state.terminal = "known-checkpoint"
    requests = [{"text": "accepted", "accepted": True}, {"text": "unaccepted", "accepted": False}]
    assert state.recovery(requests)["unaccepted_instructions"] == ["unaccepted"]
    state.accepted_parent = "unfinished"
    with pytest.raises(SteeringUnresolved, match="reconcile"):
        state.recovery(requests)
    state.required = {"missing"}
    with pytest.raises(ValueError, match="missing"):
        state.outputs([])
    with pytest.raises(ValueError, match="recognizes"):
        parse("approve things when they seem expensive")


async def test_disconnect_retrieves_terminal_then_reconnects_without_duplicate_output(tmp_path):
    bridge, sent, accounted = await setup(tmp_path, [])
    bridge.initial_steer = None
    bridge.state.current = "known-terminal"
    bridge.requests = [{"text": TEXT, "accepted": False, "sent": True}]
    terminal = Event(id="known-terminal", status="completed", output=[Event(type="function_call")])
    retrieved = []

    async def retrieve(rid):
        retrieved.append(rid)
        return terminal

    async def close():
        pass

    async def connect():
        sent.append(("reconnect", {}))

    bridge.client = SimpleNamespace(responses=SimpleNamespace(retrieve=retrieve))
    bridge.close = close
    bridge.connect = connect
    bridge.state.outputs([{"type": "function_call_output", "call_id": "already-sent"}])
    assert await bridge.recover_terminal("record") is terminal
    assert retrieved == ["known-terminal"]
    assert sent == [("reconnect", {})]
    assert accounted == ["known-terminal"]
    assert bridge.requests[0]["sent"] is False
    assert bridge.state.delivered == {"already-sent"}


async def test_disconnect_with_unfinished_response_does_not_replay(tmp_path):
    bridge, sent, _ = await setup(tmp_path, [])
    bridge.state.current = "in-flight"

    async def retrieve(rid):
        return Event(id=rid, status="in_progress")

    bridge.client = SimpleNamespace(responses=SimpleNamespace(retrieve=retrieve))
    with pytest.raises(SteeringUnresolved, match="not a confirmed terminal"):
        await bridge.recover_terminal("record")
    assert sent == []
