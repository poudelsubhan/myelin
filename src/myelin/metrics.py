from decimal import Decimal


def aggregate(records):
    unique = {r.response_id: r for r in records}
    purposes = {}
    for row in unique.values():
        group = purposes.setdefault(
            row.purpose, {"calls": 0, "usd": Decimal(0), "input_tokens": 0, "output_tokens": 0}
        )
        group["calls"] += row.model_calls
        group["input_tokens"] += row.input_tokens
        group["output_tokens"] += row.output_tokens
        group["usd"] = (
            group["usd"] + Decimal(row.usd)
            if group["usd"] is not None and row.usd is not None
            else None
        )
    return {
        k: {**v, "usd": str(v["usd"]) if v["usd"] is not None else None}
        for k, v in purposes.items()
    }


def amortized(setup_usd, execution_usd, count):
    if count <= 0 or setup_usd is None or execution_usd is None:
        return None
    return str(Decimal(setup_usd) / count + Decimal(execution_usd))
