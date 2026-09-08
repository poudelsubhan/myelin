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


def test_native_validation_is_owned_by_server_job_and_event_bus(monkeypatch, tmp_path):
    monkeypatch.setenv("MYELIN_RUNS_DIR", str(tmp_path))

    async def native(settings, bus, candidates, request, run_id):
        await bus.emit(run_id, "compiler.analysis", {"fixture": True})
        return {"fixture": True, "candidate_hash": request.candidate_hash}

    monkeypatch.setattr("myelin.astra.native_validation.native_validation", native)
    request = {
        "candidate_hash": "a" * 64,
        "workflow": "crm.create_invoice",
        "environment": {"app": "crm", "revision": "crm-v1"},
        "policy_revision": "crm-policy-v1",
        "suite_hash": "fixture",
        "reference_mode": "full",
        "reference_case_ids": [f"c{i}" for i in range(1, 9)],
    }
    with TestClient(create_app()) as client:
        assert (
            client.post(
                "/native-gates", json={"request": request | {"reference_mode": "none"}}
            ).status_code
            == 422
        )
        response = client.post("/native-gates", json={"request": request})
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        for _ in range(50):
            status = client.get(f"/runs/{run_id}").json()
            if status["status"] != "running":
                break
            time.sleep(0.01)
        assert status["status"] == "completed" and status["evidence"]["fixture"]
        assert "compiler.analysis" in (tmp_path / run_id / "events.jsonl").read_text()


def test_completed_run_history_survives_restart_without_restarting_jobs(monkeypatch, tmp_path):
    import json

    monkeypatch.setenv("MYELIN_RUNS_DIR", str(tmp_path))
    folder = tmp_path / "saved-run"
    folder.mkdir()
    (folder / "status.json").write_text(
        json.dumps({"status": "completed", "result": {"model_calls": 0}})
    )
    with TestClient(create_app()) as client:
        assert client.get("/runs/saved-run").json()["result"]["model_calls"] == 0
        assert client.get("/runs").json()[0]["run_id"] == "saved-run"
