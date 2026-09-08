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


def test_usage_counts_do_not_become_secrets_or_corrupt_ids():
    sanitizer = Sanitizer()
    row = {
        "input_tokens": 1200,
        "output_tokens": 6,
        "cached_tokens": 0,
        "run_id": "a60-1200",
        "model_usd": "0.062",
    }
    assert sanitizer.clean(row) == row
    secret = sanitizer.clean({"csrf": "long-csrf-value"})
    assert sanitizer.clean(secret) == secret
    assert sanitizer.clean(row) == row
