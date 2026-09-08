"""Server-owned native async validation and independent compiler analysis."""

import asyncio
import json
import time
from uuid import uuid4

from myelin.astra.async_tools import GateToolProvider
from myelin.astra.client import Astra
from myelin.gate.gate import GateRunner
from myelin.gate.runtime import GateRuntime
from myelin.trace.store import TraceStore


async def native_validation(settings, bus, candidates, request, run_id):
    store = TraceStore(settings.runs_dir, run_id)
    digest = request.candidate_hash
    program = candidates.get(request.workflow, digest)

    async def emit(kind, payload):
        return await bus.emit(run_id, kind, store.sanitizer.clean(payload))

    await emit("run.started", {"mode": "native_validation", "candidate_hash": digest})
    runtime = GateRuntime(settings, bus)

    async def remote_gate(request):
        gate_id = str(uuid4())
        store.save("gate-id.json", {"gate_id": gate_id})
        print(json.dumps({"run_id": run_id, "gate_id": gate_id, "status": "pending"}), flush=True)
        return await GateRunner(
            settings.runs_dir / "gates",
            candidates,
            runtime.program,
            emit,
            runtime.reference,
            runtime.outcome,
        )(request, gate_id)

    provider = GateToolProvider(remote_gate, request, store)
    try:
        astra = Astra(settings, store, emit)
        instructions = (
            "Use validate_candidate exactly once on the supplied immutable hash, "
            "choosing a unique task_handle. This is a native async gate; never invent "
            "its result. Independent analysis can proceed while validation runs."
        )
        first = await astra.respond(
            "compile",
            instructions=instructions,
            reasoning={"effort": "medium"},
            tools=provider.tools()[:1],
            tool_choice={"type": "function", "name": "validate_candidate"},
            input=[
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "candidate_hash": digest,
                            "task": "Launch the full eight-case program/reference gate.",
                        }
                    ),
                }
            ],
        )
        store.save("first-response.json", first.model_dump(mode="json"))
        calls = [c for c in first.output if c.type == "function_call"]
        assert len(calls) == 1 and calls[0].async_, "Native async launch not observed"
        store.save("native-call.json", calls[0].model_dump(mode="json"))
        assert await provider.execute(calls[0]) == []
        launched = time.time()
        # Real, useful analysis of this candidate while the gate owns its immutable bytes.
        analysis = await astra.respond(
            "compile",
            instructions=instructions,
            reasoning={"effort": "medium"},
            tools=provider.tools()[:1],
            tool_choice="none",
            previous_response_id=first.id,
            input=astra.effort_update(
                "high",
                "While the gate runs, analyze these actual dependencies. Identify the response "
                "fields that keep auth and entity identity fresh, and evidence limitations. "
                "Do not infer gate success. Program: " + program.canonical_json(),
            ),
        )
        pending_during_analysis = any(not c.job.done() for c in provider.registry.calls.values())
        await emit(
            "compiler.analysis",
            {
                "response_id": analysis.id,
                "text": analysis.output_text,
                "gate_pending": pending_during_analysis,
            },
        )
        store.save(
            "independent-analysis.json",
            {
                "response_id": analysis.id,
                "text": analysis.output_text,
                "pending_during_analysis": pending_during_analysis,
                "launched_at": launched,
                "analysis_finished_at": time.time(),
            },
        )
        print(
            json.dumps(
                {"analysis_response": analysis.id, "gate_still_pending": pending_during_analysis}
            ),
            flush=True,
        )
        await asyncio.gather(*(c.job for c in provider.registry.calls.values()))
        outputs = provider.registry.completed()
        assert len(outputs) == 1 and outputs[0]["call_id"] == calls[0].call_id
        assert provider.registry.completed() == []
        final = await astra.respond(
            "compile",
            instructions=instructions,
            reasoning={"effort": "medium"},
            tools=provider.tools()[:1],
            tool_choice="none",
            previous_response_id=analysis.id,
            input=outputs,
        )
        gate = next(iter(provider.registry.calls.values())).job.result()
        evidence = {
            "run_id": run_id,
            "candidate_hash": digest,
            "gate_id": gate.gate_id,
            "native_call_id": calls[0].call_id,
            "response_ids": [first.id, analysis.id, final.id],
            "terminal_deliveries": len(outputs),
            "pending_during_analysis": pending_during_analysis,
            "oracle_passes": gate.oracle_passes,
            "reference_passes": gate.reference_passes,
            "reference_total": gate.reference_total,
            "all_references_complete": gate.all_references_complete,
            "effort_update_accepted": astra.effective_efforts[analysis.id] == "high",
            "status": gate.status,
        }
        store.save("evidence.json", evidence)
        print(json.dumps(evidence), flush=True)
        assert gate.status == "passed" and gate.reference_passes == 8 and pending_during_analysis
        await emit("run.finished", {"status": "completed", "evidence": evidence})
        return evidence
    finally:
        undelivered = await provider.registry.close()
        if undelivered:
            store.save("interrupted-tool-outputs.json", undelivered)
