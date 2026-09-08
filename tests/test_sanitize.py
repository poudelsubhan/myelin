import json

from myelin.trace.sanitize import Sanitizer


def test_tokens_redacted_but_business_values_preserved():
    sanitizer = Sanitizer()
    raw = {
        "request": {"password": "private-password", "total_cents": 12345},
        "html": '<input name="csrf" value="random-csrf">',
        "headers": {"set-cookie": "session=private-cookie"},
    }
    result = sanitizer.clean(raw)
    serialized = json.dumps(result)
    assert "private-password" not in serialized
    assert "random-csrf" not in serialized
    assert "private-cookie" not in serialized
    assert result["request"]["total_cents"] == 12345
