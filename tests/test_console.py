import time

from fastapi.testclient import TestClient

from myelin.console.app import create_app
from myelin.contracts import Services
from myelin.schema import Trace


async def fake_record(workflow, inputs, environment):
    return Trace(
        run_id="fixture",
        workflow=workflow,
        inputs=inputs,
        environment=environment,
        policy_revision="crm-policy-v1",
        initial_observation="o",
        actions=[],
        usage_response_ids=[],
        steer_marks=[],
        outcome="awaiting_verification",
        final_observation="o",
    )


def test_cli_style_run_appears_in_server_and_unavailable_modes_fail(monkeypatch, tmp_path):
    monkeypatch.setenv("MYELIN_RUNS_DIR", str(tmp_path))
    with TestClient(create_app(Services(recorder=fake_record))) as client:
        body = {
            "workflow": "crm.create_invoice",
            "inputs": {},
            "mode": "record",
            "environment": {"app": "crm", "revision": "crm-v1"},
        }
        response = client.post("/runs", json=body)
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        for _ in range(50):
            status = client.get(f"/runs/{run_id}").json()
            if status["status"] != "running":
                break
            time.sleep(0.01)
        assert status["status"] == "awaiting_verification"
        assert client.get("/runs").json()[0]["run_id"] == run_id
        assert client.post("/runs", json=body | {"mode": "full"}).status_code == 501
        assert client.get("/events?run_id=../secret").status_code == 422
