"""Orchestrator-owned hosted analysis, staged patch, full gate and promotion."""

from myelin.astra.compiler_tools import CompilerTools
from myelin.gate.gate import GateRunner, suite, suite_hash
from myelin.gate.runtime import GateRuntime
from myelin.program.compiler import Compiler
from myelin.program.lifter import lift
from myelin.schema import GateRequest, NetworkEvent, Trace
from myelin.trace.store import TraceStore


async def hosted_compilation(
    settings, bus, candidates, ledger, workflow, digest, run_id, full=True
):
    program = candidates.get(workflow, digest)
    settings = settings.for_app(program.app)
    store = TraceStore(settings.runs_dir, run_id)

    async def emit(kind, payload):
        return await bus.emit(run_id, kind, store.sanitizer.clean(payload))

    await emit("run.started", {"mode": "hosted_compilation", "candidate_hash": digest})
    traces, network, http_candidates = [], [], []
    for source_id in program.compiled_from:
        folder = settings.runs_dir / source_id
        trace_file = folder / "trace.json"
        if not trace_file.exists():
            continue
        trace = Trace.model_validate_json(trace_file.read_text())
        rows = [
            NetworkEvent.model_validate_json(line)
            for line in (folder / "network.jsonl").read_text().splitlines()
        ]
        traces.append(trace)
        network.extend(rows)
        http_candidates.extend(lift(trace, trace.inputs, rows))
    if not traces:
        raise ValueError("actual source trace is required for hosted analysis")
    trace = traces[-1].model_copy(deep=True)
    trace.actions = list({a.id: a for t in traces for a in t.actions}.values())
    trace.steer_marks = list({m.id: m for t in traces for m in t.steer_marks}.values())
    store.save("source-program.json", program)
    tools = CompilerTools(settings, store, emit, program)
    analysis = await tools.prepare(trace, network)
    candidate = await Compiler(settings, store, emit)(
        trace, http_candidates, prior=program, tool_provider=tools
    )
    if candidate.steps != program.steps or candidate.final_post != program.final_post:
        raise ValueError("this analysis patch must preserve all executable behavior")
    candidate_hash = candidates.put(candidate)
    request = GateRequest(
        candidate_hash=candidate_hash,
        workflow=workflow,
        environment=trace.environment,
        policy_revision=candidate.policy_revision,
        suite_hash=suite_hash(workflow, candidate.policy_revision),
        reference_mode="full" if full else "none",
        reference_case_ids=[c.case_id for c in suite(workflow)] if full else [],
    )
    runtime = GateRuntime(settings, bus)
    runner = GateRunner(
        settings.runs_dir / "gates",
        candidates,
        runtime.program,
        emit,
        runtime.reference,
        runtime.outcome,
    )
    gate = await runner(request)
    # Full model comparisons establish correctness for the changed hash; a fresh zero-model
    # baseline establishes the deterministic work comparison without duplicate reference calls.
    baseline = await runner(
        request.model_copy(
            update={"candidate_hash": digest, "reference_mode": "none", "reference_case_ids": []}
        )
    )
    decision = await ledger.promote(
        gate, digest, "optimization", [c.case_id for c in suite(workflow)], baseline=baseline
    )
    await emit("ledger.updated", decision.model_dump(mode="json"))
    evidence = {
        "run_id": run_id,
        "source_hash": digest,
        "candidate_hash": candidate_hash,
        "container_id": analysis["container_id"],
        "hosted_response_ids": analysis["response_ids"],
        "patch_calls": tools.patch_calls,
        "gate_id": gate.gate_id,
        "baseline_gate_id": baseline.gate_id,
        "oracle_passes": gate.oracle_passes,
        "reference_passes": gate.reference_passes,
        "decision": decision.model_dump(mode="json"),
        "behavior_preserved": True,
        "passed": gate.status == "passed" and decision.verdict == "promoted",
    }
    store.save("evidence.json", evidence)
    await emit(
        "run.finished",
        {"status": "completed" if evidence["passed"] else "failed", "evidence": evidence},
    )
    return evidence
