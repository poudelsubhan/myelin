"""Audit saved live evidence by default. Remote writes require --execute + a fixed manifest."""

import argparse
import asyncio
import json
from pathlib import Path

from myelin.config import ROOT, Settings
from myelin.live.journal import Journal
from myelin.live.ledger import LiveLedger
from myelin.runtime.registry import Registry


def audit(root, workflow_id, manifest):
    registry = Registry(root)
    spec = registry.workflow(workflow_id)
    ledger = LiveLedger(root / "ledger", Journal(root / "live.sqlite"))
    keys = [manifest["learn"]["task_key"]] + [
        r["task_key"] for r in manifest["canaries"] + manifest["batch"]
    ]
    statuses = [json.loads(p.read_text()) for p in (root / "runs").glob("*/status.json")]
    required = {a.name for a in spec.outcome_contract if a.required}
    results = []
    for key in keys:
        runs = [
            r
            for r in statuses
            if r.get("workflow_id") == workflow_id
            and r.get("task_key") == key
            and r.get("status") == "verified"
        ]
        if not runs:
            results.append({"task_key": key, "status": "missing_verified_evidence"})
            continue
        run = max(runs, key=lambda r: r.get("evidence", {}).get("captured_at", 0))
        evidence = run["evidence"]
        passed = {a["name"] for a in evidence["assertions"] if a["passed"]}
        good = (
            evidence["status"] == "verified"
            and required <= passed
            and len(evidence["resource_urls"]) == 1
        )
        if key != keys[0]:
            good = good and run["model_calls"] == 0
        if run.get("candidate_hash"):
            ledger.get(run["candidate_hash"])
        results.append(
            {
                "task_key": key,
                "status": "verified" if good else "failed",
                "urls": evidence["resource_urls"],
                "model_calls": run["model_calls"],
                "wall_ms": run["wall_ms"],
                "candidate_hash": run.get("candidate_hash"),
                "captured_at": evidence["captured_at"],
                "assertions": len(required),
            }
        )
    return {
        "status": "verified" if all(r["status"] == "verified" for r in results) else "incomplete",
        "basis": "saved independently verified evidence; no remote writes or model calls",
        "runs": results,
    }


async def execute(root, manifest, readback=False):
    from myelin.live.orchestration import LiveOrchestrator

    engine = LiveOrchestrator(Settings.load(), root)
    try:
        if readback:
            return await engine.readback(
                manifest["workflow_id"],
                manifest["profile_id"],
                [manifest["learn"]] + manifest["canaries"] + manifest["batch"],
            )
        return await engine.batch(
            manifest["workflow_id"], manifest["profile_id"], manifest["batch"]
        )
    finally:
        await engine.profiles.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--readback",
        action="store_true",
        help="Fresh read-only verification in the connected browser",
    )
    parser.add_argument("--assert", dest="assert_complete", action="store_true")
    args = parser.parse_args()
    root = ROOT / ".local/myelin"
    if args.execute and not args.manifest:
        parser.error("--execute requires an explicit fixed --manifest")
    manifest = json.loads((args.manifest or root / "manifest.json").read_text())
    if manifest["workflow_id"] != args.workflow:
        parser.error("manifest and requested workflow differ")
    result = (
        asyncio.run(execute(root, manifest, readback=args.readback))
        if args.execute or args.readback
        else audit(root, args.workflow, manifest)
    )
    print(json.dumps(result, indent=2))
    if args.assert_complete and result["status"] != "verified":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
