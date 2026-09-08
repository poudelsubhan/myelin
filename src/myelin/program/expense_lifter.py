"""Expense JSON lifting grounded in login token, response ID, and declared adapter fields."""

from urllib.parse import urlsplit

from myelin.program.lifter import exists, input_ref, literal, secret, status, variable
from myelin.schema import ExtractRule, HttpCandidate, HttpStep


def lift(trace, inputs, network):
    actions = {a.id: a for a in trace.actions if a.outcome == "success"}
    login = created = None
    candidates = []

    def add(step, event, prerequisites=()):
        candidates.append(
            HttpCandidate(
                id=step.id,
                source_action_ids=step.source_action_ids,
                request_ids=[event.request_id],
                prerequisite_ids=list(prerequisites),
                step=step,
                confidence="high",
                evidence=[
                    "Observed JSON request and successful response",
                    "Declared expense adapter maps fresh login token and response-derived ID",
                    f"Observed source request {event.request_id}",
                ],
                unresolved=[],
            )
        )

    for event in sorted(network, key=lambda e: e.timestamp):
        if (
            event.test_traffic
            or event.response_status != 200
            or not isinstance(event.response_body, dict)
        ):
            continue
        path = urlsplit(event.url).path
        if (
            event.method == "POST"
            and path == "/api/login"
            and isinstance(event.response_body.get("token"), str)
        ):
            login = event
            if (
                event.action_id in actions
                and isinstance(event.request_body, dict)
                and set(event.request_body) == {"email", "password", "tenant"}
            ):
                step = HttpStep(
                    id="expense-login",
                    intent="Authenticate a fresh expense session",
                    source_action_ids=[event.action_id],
                    pre=[exists("input", "merchant")],
                    post=[status(200)],
                    effect="write",
                    operation_key="expense-login",
                    method="POST",
                    url=literal("/api/login"),
                    body_kind="json",
                    body={k: secret(k) for k in ("email", "password", "tenant")},
                    extract=[
                        ExtractRule(
                            target_var="bearer_token",
                            source="json_path",
                            expression="$.token",
                            secret=True,
                        )
                    ],
                )
                add(step, event)
            continue
        if login is None or event.action_id not in actions:
            continue
        auth = next(
            (v for k, v in event.request_headers.items() if k.lower() == "authorization"), ""
        )
        if auth != "Bearer " + login.response_body["token"]:
            continue
        headers = {"Authorization": secret("bearer_token")}
        if event.method == "POST" and path == "/api/expenses":
            body = event.request_body
            if not isinstance(body, dict):
                continue
            amount_key = (
                "cost_cents"
                if "rename_field:amount" in trace.environment.mutations
                else "amount_cents"
            )
            expected = {"merchant", amount_key, "category", "date", "receipt_text"}
            if set(body) != expected or any(
                body[k] != inputs["amount_cents" if k == amount_key else k] for k in body
            ):
                continue
            result = event.response_body
            if (
                not isinstance(result.get("id"), str)
                or result.get("resume_url") != "/#" + result["id"]
            ):
                continue
            created = event
            step = HttpStep(
                id="expense-create",
                intent=actions[event.action_id].intent,
                source_action_ids=[event.action_id],
                pre=[exists("input", "merchant")],
                post=[status(200)],
                effect="write",
                operation_key="expense-create",
                depends_on=["expense-login"],
                method="POST",
                url=literal("/api/expenses"),
                headers=headers,
                body_kind="json",
                body={k: input_ref("amount_cents" if k == amount_key else k) for k in body},
                resume_url=literal("/#new"),
                extract=[
                    ExtractRule(target_var="expense_id", source="json_path", expression="$.id"),
                    ExtractRule(
                        target_var="expense_resume", source="json_path", expression="$.resume_url"
                    ),
                ],
            )
            add(step, event, ["expense-login"])
        elif (
            event.method == "POST"
            and created
            and path == "/api/expenses/" + created.response_body["id"] + "/submit"
        ):
            body = event.request_body
            # Steering policy retains UI note/confirmation actions and one common submit.
            if trace.policy_revision != "expense-policy-v1" or body not in (
                {},
                {"manager_note": ""},
            ):
                continue
            step = HttpStep(
                id="expense-submit",
                intent=actions[event.action_id].intent,
                source_action_ids=[event.action_id],
                pre=[exists("variable", "expense_id")],
                post=[status(200)],
                effect="write",
                operation_key="expense-submit",
                depends_on=["expense-create"],
                method="POST",
                url=literal("/api/expenses/{id}/submit"),
                path_params={"id": variable("expense_id")},
                headers=headers,
                body_kind="json",
                body={k: literal(v) for k, v in body.items()},
                resume_url=variable("expense_resume"),
            )
            add(step, event, ["expense-create"])
    return candidates
