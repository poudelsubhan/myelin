"""Conservative form lifting from observed request and HTML token-source evidence."""

from urllib.parse import urlsplit

from myelin.program.bindings import BindingError, html_input
from myelin.schema import (
    Compare,
    ExtractRule,
    HttpCandidate,
    HttpStep,
    LiteralRef,
    MultiplyRef,
    NamedRef,
)


def literal(value):
    return LiteralRef(value=value)


def variable(key):
    return NamedRef(kind="variable", key=key)


def secret(key):
    return NamedRef(kind="secret", key=key)


def input_ref(key):
    return NamedRef(kind="input", key=key)


def status(code):
    return Compare(source="response", path="status", op="eq", expected=literal(code))


def exists(source, path):
    return Compare(source=source, path=path, op="exists", expected=literal(True))


def bind_field(key, value, inputs):
    """Field identity disambiguates repeated equal inputs; value alone is insufficient."""
    if key in inputs and str(inputs[key]) == str(value):
        return input_ref(key)
    if key in ("total_cents", "amount_due_cents") and str(
        inputs.get("quantity", 0) * inputs.get("unit_price_cents", 0)
    ) == str(value):
        return MultiplyRef(left=input_ref("quantity"), right=input_ref("unit_price_cents"))
    matches = [k for k, v in inputs.items() if str(v) == str(value)]
    if matches:
        raise BindingError(f"ambiguous field {key}: value matches {matches} without field evidence")
    raise BindingError(f"unexplained business field: {key}")


def lift(trace, inputs, network):
    candidates = []
    actions = {a.id: a for a in trace.actions}
    reads = []
    for event in sorted(network, key=lambda e: e.timestamp):
        if event.test_traffic or event.resource_type not in ("document", "fetch", "xhr"):
            continue
        if event.response_status is None or event.response_status >= 400:
            continue
        if event.method == "GET":
            reads.append(event)
            continue
        if event.method != "POST" or event.action_id not in actions:
            continue
        body = event.request_body
        if not isinstance(body, dict) or "csrf" not in body:
            continue  # Auth remains UI; unsupported writes retain their recorded UI actions.
        source = None
        for read in reversed(reads):
            if not isinstance(read.response_body, str):
                continue
            try:
                token = html_input(read.response_body, "csrf")
                action_url = html_input(read.response_body, "action_url")
                if token == body["csrf"] and action_url == urlsplit(event.url).path:
                    source = read
                    break
            except BindingError:
                continue
        if source is None:
            continue
        key = "invoice" if "description" in body else "payment"
        fetch_id, write_id = f"{key}-form", f"{key}-write"
        unresolved = []
        bindings = {"csrf": secret(f"{key}_csrf")}
        for field, value in body.items():
            if field in ("csrf", "operation_id", "action_url"):
                continue
            try:
                bindings[field] = bind_field(field, value, inputs)
            except BindingError as exc:
                unresolved.append(str(exc))
        source_ids = [source.action_id] if source.action_id in actions else [event.action_id]
        fetch = HttpStep(
            id=fetch_id,
            intent=f"Fetch fresh {key} token and action URL",
            source_action_ids=source_ids,
            pre=[exists("variable", "current_url")]
            if key == "invoice"
            else [exists("variable", "invoice_location")],
            post=[status(200)],
            effect="read",
            method="GET",
            url=variable("current_url" if key == "invoice" else "invoice_location"),
            body_kind="none",
            extract=[
                ExtractRule(
                    target_var=f"{key}_csrf", source="html_input", expression="csrf", secret=True
                ),
                ExtractRule(
                    target_var=f"{key}_action", source="html_input", expression="action_url"
                ),
            ],
        )
        mutation = HttpStep(
            id=write_id,
            intent=actions[event.action_id].intent,
            source_action_ids=[event.action_id],
            pre=[exists("variable", f"{key}_action")],
            post=[status(event.response_status)],
            effect="write",
            operation_key=write_id,
            depends_on=[fetch_id],
            resume_url=variable("current_url" if key == "invoice" else "invoice_location"),
            method="POST",
            url=variable(f"{key}_action"),
            body_kind="form",
            body=bindings,
            extract=[
                ExtractRule(target_var="invoice_location", source="header", expression="location")
            ]
            if key == "invoice"
            else [],
        )
        for step, requests, prereqs in [
            (fetch, [source.request_id], []),
            (mutation, [event.request_id], [fetch_id]),
        ]:
            candidates.append(
                HttpCandidate(
                    id=step.id,
                    source_action_ids=step.source_action_ids,
                    request_ids=requests,
                    prerequisite_ids=prereqs,
                    step=step,
                    confidence="low" if unresolved else "high",
                    unresolved=unresolved,
                    evidence=[
                        f"Observed {event.method} {urlsplit(event.url).path}",
                        f"CSRF and action_url from response {source.request_id}",
                        "Field-level bindings; no recorded IDs or token literals",
                    ],
                )
            )
    return candidates
