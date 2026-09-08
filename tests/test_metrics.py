from myelin.metrics import aggregate, amortized
from myelin.schema import UsageRecord


def test_dedupe_unknown_prices_and_zero_denominators():
    row = UsageRecord(
        response_id="r1",
        purpose="record",
        model="fixture",
        input_tokens=100,
        cached_tokens=0,
        output_tokens=10,
        model_calls=1,
        usd="0.10",
    )
    assert aggregate([row, row])["record"] == {
        "calls": 1,
        "usd": "0.10",
        "input_tokens": 100,
        "output_tokens": 10,
    }
    assert aggregate([row.model_copy(update={"usd": None})])["record"]["usd"] is None
    assert amortized("1", "0", 10) == "0.1"
    assert amortized("1", "0", 0) is None
