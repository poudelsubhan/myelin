"""Observed same-session HTTP execution, with fresh cookies and durable write guards."""

import json
from urllib.parse import urlsplit

from myelin.live.schema import digest
from myelin.program.bindings import bound_url, resolve


async def prepare(session, step, evidence, inputs):
    path = session.store.folder.parent / evidence.source_run / "network.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    event = next((e for e in events if e["request_id"] == evidence.request_id), None)
    if not event or digest(event) != evidence.request_hash:
        raise ValueError("observed HTTP provenance is missing or changed")
    if (
        event["action_id"] != evidence.source_action_id
        or evidence.source_action_id not in step.source_action_ids
    ):
        raise ValueError("HTTP source action mismatch")

    def resolver(ref):
        return resolve(ref, inputs, session.variables, session.secrets)

    url = bound_url(resolver(step.url), {k: resolver(v) for k, v in step.path_params.items()})
    if urlsplit(url).netloc != urlsplit(event["url"]).netloc or step.method != event["method"]:
        raise ValueError("HTTP origin or method differs from observed evidence")
    session.origin_policy.permit(step.method, url, "fetch")
    if step.headers or step.query or step.body_kind != "json":
        raise ValueError("unsupported HTTP header/query/auth transport")
    # Every payload field must have been observed. Constants remain byte-equivalent;
    # dynamic business refs are independently checked by the frozen effect boundary.
    observed = event["request_body"]
    if set(step.body) != set(observed):
        raise ValueError("unexplained HTTP request field")
    for key, ref in step.body.items():
        if ref.kind == "literal" and ref.value != observed[key]:
            raise ValueError("unexplained HTTP literal")
        if ref.kind == "secret":
            name = evidence.cookie_bindings.get(ref.key)
            if not name or observed[key] != f"<secret:{name}>":
                raise ValueError("credential source must match observed redaction provenance")
            cookies = [c for c in await session.context.cookies(url) if c["name"] == name]
            if len(cookies) != 1 or not cookies[0]["value"]:
                raise ValueError("current browser credential unavailable")
            session.secrets[ref.key] = cookies[0]["value"]
            session.store.sanitizer.register(cookies[0]["value"], name)
    session.live_http_step = step.id


async def execute(session, method, url, headers, body):
    if not getattr(session, "live_http_step", None) or not session.effects:
        raise ValueError("HTTP execution requires an observed recipe")
    step_id, session.live_http_step = session.live_http_step, None
    key, session.next_effect_key = session.next_effect_key, None
    if not await session.effects.begin(step_id, key):
        return {"status": 200, "headers": {}, "body": {}, "url": url}
    session.store.save("effect-bindings.json", session.effects.action_bindings)
    error = None
    try:
        session.origin_policy.permit(method, url, "fetch")
        if headers:
            raise ValueError("cross-origin credential forwarding is prohibited")
        await session.effects.authorize_request(session, method, url, body)
        session.http_requests += 1
        response = await session.context.request.fetch(
            url, method=method, data=body, max_redirects=0
        )
        session.store.append(
            "live-http.jsonl",
            {
                "step_id": step_id,
                "method": method,
                "url": url,
                "status": response.status,
                "effect_key": key,
            },
        )
        # Status alone never decides whether the write applied. Read-back does.
        return {"status": response.status, "headers": {}, "body": {}, "url": url}
    except BaseException as exc:
        error = exc
        raise
    finally:
        await session.effects.finish(session, error)
