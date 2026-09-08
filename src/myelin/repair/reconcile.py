from urllib.parse import quote
from uuid import uuid4


async def reconcile(session, failure):
    if failure.effect_status != "unknown":
        return {"status": failure.effect_status, "result": None}
    op = failure.checkpoint.operation_id
    if not op:
        return {"status": "unknown", "reason": "No operation identity"}
    response = await session.request(
        str(uuid4()),
        "GET",
        session.settings.crm_url + "/api/operations/" + quote(op, safe=""),
        {"Authorization": session.secrets["bearer_token"]}
        if session.environment.app == "expense" and "bearer_token" in session.secrets
        else {},
        None,
    )
    if response["status"] != 200:
        return {"status": "unknown", "reason": "Operation lookup unavailable"}
    body = response["body"]
    if body["status"] == "applied":
        return {"status": "applied", "result": body["result"], "request_hash": body["request_hash"]}
    # Absent does not establish that a request still in transit cannot arrive later.
    return {"status": "unknown", "reason": "Original handler completion not established"}
