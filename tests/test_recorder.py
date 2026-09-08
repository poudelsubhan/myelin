import json
from types import SimpleNamespace

from myelin.contracts import RawObservation
from myelin.schema import EnvironmentSpec
from myelin.trace.store import TraceStore
from myelin.vision.harness import Recorder
from tests.fakes import FakeBrowserSession


class RecordingSession(FakeBrowserSession):
    def __init__(self, tmp_path):
        super().__init__()
        self.run_id = "scripted-recorder"
        self.store = TraceStore(tmp_path, self.run_id)
        self.variables = {}
        self.secrets = {}
        self.network = []

    async def snapshot(self):
        return RawObservation(self.url, b"fake-frame", "heading: " + self.url)


async def test_scripted_model_preserves_each_action_and_requires_oracle(monkeypatch, tmp_path):
    calls = []

    class FakeAstra:
        def __init__(self, *args):
            self.usage = {}

        async def respond(self, purpose, **kwargs):
            calls.append(kwargs)
            output = [
                SimpleNamespace(
                    type="function_call",
                    name="browser_batch",
                    call_id="call-1",
                    arguments=json.dumps(
                        {
                            "actions": [
                                {
                                    "intent": "open",
                                    "operation": "navigate",
                                    "arguments": {
                                        "url": {
                                            "kind": "literal",
                                            "value": "http://localhost:8101/login",
                                        }
                                    },
                                },
                                {"intent": "inspect", "operation": "inspect", "arguments": {}},
                            ]
                        }
                    ),
                )
            ]
            return SimpleNamespace(
                id=f"response-{len(calls)}",
                status="completed",
                output=output if len(calls) == 1 else [],
            )

    monkeypatch.setattr("myelin.vision.harness.Astra", FakeAstra)
    session = RecordingSession(tmp_path)
    events = []

    async def emit(kind, payload):
        events.append((kind, payload))

    settings = SimpleNamespace(timeout_s=10, crm_url="http://localhost:8101")
    trace = await Recorder(settings, emit)(
        "crm.create_invoice", {}, EnvironmentSpec(app="crm", revision="crm-v1"), session
    )
    assert trace.outcome == "awaiting_verification"
    assert len(trace.actions) == 2
    assert len({a.id for a in trace.actions}) == 2
    assert {a.call_id for a in trace.actions} == {"call-1"}
    assert all(a.before_id != a.after_id for a in trace.actions)
    assert calls[1]["previous_response_id"] == "response-1"
    assert session.closed is False
