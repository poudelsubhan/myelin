"""Small deterministic binding language; no eval, no model-assisted input parsing."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator

from myelin.program.bindings import resolve


def derive(spec, raw):
    Draft202012Validator(spec.input_schema).validate(raw)
    result = dict(raw)
    for rule in spec.derived_fields:
        if rule.target in result:
            raise ValueError("derived field cannot overwrite an input")
        values = [resolve(v, result, {}, {}) for v in rule.values]
        if rule.operation == "copy":
            (value,) = values
        elif rule.operation == "concat":
            value = "".join(str(v) for v in values)
        elif rule.operation == "list":
            value = values
        elif rule.operation == "lookup":
            mapping, key = values
            value = mapping[key]
        elif rule.operation == "format_datetime":
            stamp, fmt, zone = values
            value = datetime.fromisoformat(stamp).astimezone(ZoneInfo(zone)).strftime(fmt)
        else:
            day, local_time, zone = values
            tz = ZoneInfo(zone)
            local = datetime.combine(date.fromisoformat(day), time.fromisoformat(local_time), tz)
            value = local.isoformat()
            # Reject nonexistent or ambiguous local times rather than guessing a fold.
            if (
                local.astimezone(ZoneInfo("UTC")).astimezone(tz) != local
                or local.replace(fold=1).utcoffset() != local.utcoffset()
            ):
                raise ValueError("ambiguous or nonexistent local time")
        result[rule.target] = value
    key = result[spec.task_key_field]
    if not isinstance(key, str) or not key.strip() or len(key) > 200:
        raise ValueError("task key must be a nonempty string of at most 200 characters")
    return result
