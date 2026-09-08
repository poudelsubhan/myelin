"""Path-sensitive availability: only definitions common to both branches escape."""

from myelin.program.bindings import BindingError
from myelin.schema import Branch, Compare, HttpStep, NamedRef


def values(value):
    if isinstance(value, NamedRef):
        yield value
    elif isinstance(value, Compare):
        if value.source in ("input", "variable") and value.path:
            key = value.path.removeprefix("$.").split(".")[0].split("[")[0]
            yield NamedRef(kind=value.source, key=key)
        yield from values(value.expected)
    elif hasattr(type(value), "model_fields"):
        for key in type(value).model_fields:
            yield from values(getattr(value, key))
    elif isinstance(value, dict):
        for child in value.values():
            yield from values(child)
    elif isinstance(value, list):
        for child in value:
            yield from values(child)


def validate_dataflow(program, input_keys):
    def check(items, variables, secrets):
        for ref in values(items):
            available = {"input": input_keys, "variable": variables, "secret": secrets}[ref.kind]
            if ref.key not in available:
                raise BindingError(f"unavailable {ref.kind} binding {ref.key}")

    def walk(steps, variables, secrets, completed):
        variables, secrets, completed = set(variables), set(secrets), set(completed)
        for step in steps:
            if not set(step.depends_on) <= completed:
                raise BindingError(f"unavailable dependency for {step.id}")
            check([step.pre, step.resume_url], variables, secrets)
            if isinstance(step, Branch):
                check(step.condition, variables, secrets)
                left = walk(step.then, variables, secrets, completed)
                right = walk(step.otherwise, variables, secrets, completed)
                variables, secrets, completed = [a & b for a, b in zip(left, right, strict=True)]
            elif isinstance(step, HttpStep):
                check(
                    [step.url, step.path_params, step.query, step.headers, step.body],
                    variables,
                    secrets,
                )
                for rule in step.extract:
                    (secrets if rule.secret else variables).add(rule.target_var)
                variables.add("current_url")
            else:
                check(step.arguments, variables, secrets)
                variables.add("current_url")
            check(step.post, variables, secrets)
            completed.add(step.id)
        return variables, secrets, completed

    variables, secrets, _ = walk(program.steps, set(), {"email", "password", "tenant"}, set())
    check(program.final_post, variables, secrets)
    return program
