"""Generic goal recording and zero-call, provenance-preserving UI compilation."""

from pydantic import Field

from myelin.live.schema import LiveProgramBinding, ReadBinding, digest
from myelin.program.compiler import refs
from myelin.program.dataflow import validate_dataflow
from myelin.schema import Compare, Contract, HttpStep, LiteralRef, NamedRef, Program, UiStep
from myelin.vision.harness import BrowserAction


class LiveBrowserAction(BrowserAction):
    effect_key: str | None = None


class LiveBatch(Contract):
    actions: list[LiveBrowserAction] = Field(min_length=1, max_length=6)


def compile_ui(trace, spec, site, action_bindings, source_runs=None):
    if trace.outcome != "success":
        raise ValueError("compilation requires independent successful read-back")
    steps = []
    observed = set()
    for action in trace.actions:
        if action.outcome != "success":
            raise ValueError("failed exploration must be resolved before UI compilation")
        if action.operation == "inspect":
            continue
        key = action_bindings.get(action.id)
        if key in observed:
            raise ValueError("multiple actions cannot compile into a repeated logical effect")
        if key:
            observed.add(key)
        arguments = dict(action.arguments)
        # Recorded input refs are authoritative. Literal values can be substituted
        # only when exactly one declared input/derived value matches them.
        for name, ref in arguments.items():
            if isinstance(ref, LiteralRef):
                matches = [
                    k
                    for k, value in trace.inputs.items()
                    if type(value) is type(ref.value) and value == ref.value
                ]
                if len(matches) == 1:
                    arguments[name] = NamedRef(kind="input", key=matches[0])
        if action.target and "target_value" not in arguments:
            matches = [k for k, value in trace.inputs.items() if value == action.target.value]
            if len(matches) == 1:
                arguments["target_value"] = NamedRef(kind="input", key=matches[0])
        predicate = Compare(
            source="input", path=spec.task_key_field, op="exists", expected=LiteralRef(value=True)
        )
        steps.append(
            UiStep(
                id="ui-" + action.id,
                intent=action.intent,
                source_action_ids=[action.id],
                pre=[predicate],
                post=[predicate],
                effect="write" if key else "read",
                operation_key=key,
                depends_on=[steps[-1].id] if steps else [],
                action=action.operation,
                target=action.target,
                arguments=arguments,
            )
        )
    if observed != set(spec.allowed_effects):
        raise ValueError("recording did not cover every frozen logical effect")
    program = Program(
        workflow=spec.id,
        app=site.id,
        version=1,
        input_schema=spec.input_schema,
        policy_revision=digest(spec),
        supported_revisions=[digest(site)],
        steps=steps,
        final_post=[
            Compare(
                source="input",
                path=spec.task_key_field,
                op="exists",
                expected=LiteralRef(value=True),
            )
        ],
        compiled_from=list(dict.fromkeys((source_runs or []) + [trace.run_id])),
        notes="UI trace; external frozen read-back required",
    )
    bindings = dict(action_bindings)
    bindings.update({s.id: s.operation_key for s in steps if s.effect == "write"})
    binding = LiveProgramBinding(
        candidate_hash=program.content_hash(),
        spec_hash=digest(spec),
        site_profile_hash=digest(site),
        account_id=spec.scope["account_id"],
        workspace_id=spec.scope["workspace_id"],
        resource_scope=spec.scope["resource_id"],
        effect_bindings=bindings,
        evidence_recipe_hash=digest(spec.evidence_recipes),
        auth_profile_id=site.auth_profile_id,
    )
    recipes = {r.id: r for r in spec.evidence_recipes}
    assertions = {a.name: a for a in spec.outcome_contract}
    effects = {e.key: e for e in spec.effect_contract}
    for step in steps:
        if step.effect == "write":
            reads = []
            for name in effects[step.operation_key].assertions:
                recipe = recipes[assertions[name].recipe_id]
                if recipe.output_variable:
                    reads.append(ReadBinding(variable=recipe.output_variable, recipe_id=recipe.id))
            if reads:
                binding.after_step_reads[step.id] = reads
    validate_live_program(program, binding, spec, site, trace.inputs)
    return program, binding


def validate_live_program(program, binding, spec, site, inputs):
    from myelin.live.runtime import LiveRuntime
    from myelin.schema import EnvironmentSpec

    env = EnvironmentSpec(app=site.id, revision=digest(site), seed_version="not-applicable")
    if not LiveRuntime(spec, site, binding, None, None).compatible(program, env):
        raise ValueError("candidate envelope is incompatible with frozen scope")
    validate_dataflow(
        program,
        set(inputs),
        definitions_after=LiveRuntime(spec, site, binding, None, None).definitions_after(program),
        secret_definitions_before=LiveRuntime(
            spec, site, binding, None, None
        ).secret_definitions_before(program),
    )
    keys = []
    for step in program.steps:
        if not isinstance(step, UiStep) and not (
            isinstance(step, HttpStep) and step.id in binding.http_evidence
        ):
            raise ValueError("live baseline supports observed UI steps only")
        source_keys = {binding.effect_bindings.get(a) for a in step.source_action_ids}
        key = binding.effect_bindings.get(step.id)
        if step.effect == "write":
            if key not in spec.allowed_effects or source_keys != {key} or step.operation_key != key:
                raise ValueError("lost pre-dispatch effect provenance")
            keys.append(key)
        elif key or source_keys != {None}:
            raise ValueError("a recorded mutation cannot be reclassified as a read")
        for ref in refs(step.arguments if isinstance(step, UiStep) else step.body):
            if isinstance(ref, LiteralRef) and isinstance(ref.value, str):
                dynamic = [
                    str(v)
                    for k, v in inputs.items()
                    if k in spec.input_schema.get("properties", {}) and len(str(v)) >= 4
                ]
                if any(v in ref.value for v in dynamic):
                    raise ValueError("example-specific literal must use a declared binding")
    if len(keys) != len(set(keys)) or set(keys) != set(spec.allowed_effects):
        raise ValueError("candidate must preserve the complete effect contract")
    return program
