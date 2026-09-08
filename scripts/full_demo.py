"""Full-scope evidence audit, with explicit reuse to conserve live API usage."""

import asyncio
import hashlib
import importlib
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

from myelin.config import ROOT, Settings
from myelin.gate.ledger import CandidateStore
from myelin.verification.demo_audit import audit_core
from myelin.verification.extension_audit import audit_extensions


def replay_request(workflow, digest, environment, policy, inputs):
    # This path cannot record, compile, repair or request model references.
    return {
        "workflow": workflow,
        "mode": "program",
        "program_hash": digest,
        "environment": environment,
        "policy_revision": policy,
        "inputs": inputs,
        "compile_after": False,
        "full_validation": False,
    }


def packaging_check():
    for name in (
        "README.md",
        "docs/runbook.md",
        "docs/video.md",
        "docs/development-evidence.md",
        "docs/media/demo.mp4",
    ):
        assert (ROOT / name).is_file(), "Missing submission artifact: " + name
    manifest = json.loads((ROOT / "docs/media/manifest.json").read_text())
    assert 59 <= manifest["duration_seconds"] <= 60.1
    assert (
        hashlib.sha256((ROOT / "docs/media/demo.mp4").read_bytes()).hexdigest()
        == manifest["sha256"]
    )
    assert manifest["presentation"] == "recorded_evidence_replay"
    return {"video_sha256": manifest["sha256"], "duration_seconds": manifest["duration_seconds"]}


async def run_full(core_factory, reuse=None, stage=False):
    settings = Settings.load()
    folder = settings.runs_dir / ("full-demo-" + str(uuid4()))
    folder.mkdir(parents=True)
    evidence = {
        "demo_id": folder.name,
        "mode": "full",
        "started": time.time(),
        "model_evidence": "reused_verified_live_runs" if reuse else "fresh_live_runs",
        "new_model_calls": 0 if reuse else None,
        "smoke_runs": [],
    }

    def save():
        (folder / "evidence.json").write_text(json.dumps(evidence, indent=2))

    def beat(text):
        print(text, flush=True)
        if stage:
            input("Press Enter to continue...")

    try:
        if reuse:
            source = json.loads(Path(reuse).read_text())
        else:
            beat(
                "Full live run: this includes fresh model recording and five eight-reference gates."
            )
            core = core_factory()
            await core.core()

            async def check(name, *args):
                module = importlib.import_module((__package__ + "." if __package__ else "") + name)
                return await module.main(*args)

            expense = await check("check_phase6")
            native = await check("check_phase5")
            hosted = await check("check_hosted")
            expense_repair = await check("check_full_repairs", "expense")
            negatives = await check("check_rule_rejections")
            crm_repair = await check("check_full_repairs", "crm")
            source = {
                "core": core.evidence,
                "extensions": {
                    "expense": expense,
                    "native": native,
                    "hosted": hosted,
                    "repairs": {"crm": crm_repair, "expense": expense_repair},
                    "negatives": negatives,
                },
            }
        evidence["source"] = source
        beat("Audit traces, steering, hosted outputs, full gates and promotions.")
        evidence["core_audit"] = audit_core(source["core"], settings)
        evidence["extension_audit"] = audit_extensions(source["extensions"], settings)
        evidence["criteria"] = (
            dict(source["core"]["criteria"]) | evidence["extension_audit"]["criteria"]
        )
        assert set(evidence["criteria"]) == {f"C{i}" for i in range(1, 9)} | {
            f"E{i}" for i in range(1, 6)
        }
        assert all(evidence["criteria"].values())
        save()
        beat("Three headless tasks with zero model calls; prior full references are retained.")
        candidates = CandidateStore(ROOT / "programs")
        specs = [
            (
                "crm.create_invoice",
                source["core"]["runs"]["repair"]["candidate_hash"],
                source["core"]["runs"]["repair"]["gate_id"],
            ),
            (
                "crm.create_invoice",
                source["extensions"]["repairs"]["crm"]["candidate_hash"],
                source["extensions"]["repairs"]["crm"]["gate_id"],
            ),
            (
                "expense.submit_expense",
                source["extensions"]["repairs"]["expense"]["candidate_hash"],
                source["extensions"]["repairs"]["expense"]["gate_id"],
            ),
        ]
        async with httpx.AsyncClient(base_url="http://localhost:8100", timeout=30) as client:
            for workflow, digest, gid in specs:
                program = candidates.get(workflow, digest)
                manifest = json.loads(
                    (settings.runs_dir / "gates" / gid / "manifest.json").read_text()
                )
                inputs = (
                    {
                        "customer": "Delta Works",
                        "description": folder.name + " 'new'",
                        "quantity": 2,
                        "unit_price_cents": 12345,
                        "due_date": "2026-11-30",
                    }
                    if program.app == "crm"
                    else {
                        "merchant": folder.name + " 'new'",
                        "amount_cents": 50001,
                        "category": "Supplies",
                        "date": "2026-09-08",
                        "receipt_text": "Final rehearsal",
                    }
                )
                body = replay_request(
                    workflow, digest, manifest["environment"], program.policy_revision, inputs
                )
                response = await client.post("/runs", json=body)
                response.raise_for_status()
                rid = response.json()["run_id"]
                async with asyncio.timeout(90):
                    while True:
                        result = (await client.get("/runs/" + rid)).json()
                        if result["status"] != "running":
                            break
                        await asyncio.sleep(0.5)
                assert result["status"] == "completed" and result["result"]["model_calls"] == 0, (
                    result
                )
                assert result["result"]["model_usd"] == "0"
                assert all(a["passed"] for a in result["result"]["oracle_assertions"])
                evidence["smoke_runs"].append(
                    {
                        "run_id": rid,
                        "candidate_hash": digest,
                        "source_gate_id": gid,
                        "result": result["result"],
                    }
                )
                save()
        evidence["packaging"] = packaging_check()
        evidence["criteria"]["E6"] = True
        evidence.update(passed=True, finished=time.time())
        save()
        print(
            json.dumps(
                {
                    "demo_id": folder.name,
                    "passed": True,
                    "criteria": evidence["criteria"],
                    "model_evidence": evidence["model_evidence"],
                    "new_model_calls": evidence["new_model_calls"],
                }
            ),
            flush=True,
        )
        return evidence
    finally:
        save()
