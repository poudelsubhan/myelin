"""Lift one observed terminal UI write. Unsupported shapes retain the UI candidate."""

from myelin.live.schema import HttpEvidence, digest
from myelin.schema import HttpStep, LiteralRef, NamedRef


def lift_terminal_write(program, binding, event, source_run, spec, cookie_bindings):
    index = max(i for i, s in enumerate(program.steps) if s.effect == "write")
    step = program.steps[index]
    if (
        event["action_id"] not in step.source_action_ids
        or not 200 <= (event["response_status"] or 0) < 300
    ):
        raise ValueError("lift requires successful observed source traffic")
    effect = next(e for e in spec.effect_contract if e.key == step.operation_key)
    import re
    from urllib.parse import urlsplit

    match = re.fullmatch(effect.request.path_pattern, urlsplit(event["url"]).path)
    if not match or event["method"] != effect.request.method or effect.request.body_templates:
        raise ValueError("unsupported request shape")
    path = urlsplit(event["url"]).path
    params = {}
    for name, ref in effect.request.path_bindings.items():
        path = path.replace(match.group(name), "{" + name + "}")
        params[name] = ref
    body = {}
    for key, value in event["request_body"].items():
        if key in effect.request.required_body:
            body[key] = effect.request.required_body[key]
        elif isinstance(value, str) and value.startswith("<secret:"):
            cookie = value[len("<secret:") : -1]
            if cookie not in cookie_bindings:
                raise ValueError("fresh credential source unavailable")
            body[key] = NamedRef(kind="secret", key=cookie)
        else:
            body[key] = LiteralRef(value=value)
    fields = step.model_dump(exclude={"kind", "action", "target", "arguments"})
    lifted = HttpStep(
        **fields,
        method=event["method"],
        url=LiteralRef(value=effect.request.origin + path),
        path_params=params,
        body_kind="json",
        body=body,
    )
    result = program.model_copy(deep=True)
    result.steps = result.steps[:index] + [lifted]
    result.version += 1
    result.notes = (
        "Observed terminal HTTP write; preceding steps remain UI; independent read-back required"
    )
    envelope = binding.model_copy(deep=True)
    envelope.candidate_hash = result.content_hash()
    envelope.http_evidence[step.id] = HttpEvidence(
        source_run=source_run,
        request_id=event["request_id"],
        source_action_id=event["action_id"],
        request_hash=digest(event),
        cookie_bindings=cookie_bindings,
    )
    return result, envelope
