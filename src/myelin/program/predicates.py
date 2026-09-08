from myelin.program.bindings import BindingError, json_path, resolve
from myelin.schema import Compound


def evaluate(predicate, context, observations):
    if isinstance(predicate, Compound):
        values = [evaluate(p, context, observations) for p in predicate.items]
        return all(values) if predicate.kind == "all" else any(values)
    expected = resolve(predicate.expected, context.inputs, context.variables, context.secret_store)
    sources = {"input": context.inputs, "variable": context.variables, **observations}
    try:
        source = sources[predicate.source]
        actual = (
            json_path(source, predicate.path)
            if predicate.path.startswith("$")
            else (source[predicate.path] if predicate.path else source)
        )
    except (KeyError, BindingError, TypeError):
        if predicate.op == "exists":
            return expected is False
        return False
    if predicate.op == "exists":
        return (actual is not None) == expected
    if predicate.op in ("gt", "ge", "lt", "le"):
        if type(actual) is not type(expected) or type(actual) not in (int, str):
            raise BindingError("ordered comparison requires matching integer or string operands")
    if predicate.op == "eq":
        return type(actual) is type(expected) and actual == expected
    if predicate.op == "ne":
        return type(actual) is not type(expected) or actual != expected
    if predicate.op == "contains":
        if not isinstance(actual, (str, list, dict)):
            raise BindingError("contains requires a container")
        return expected in actual
    return {
        "gt": lambda: actual > expected,
        "ge": lambda: actual >= expected,
        "lt": lambda: actual < expected,
        "le": lambda: actual <= expected,
    }[predicate.op]()
