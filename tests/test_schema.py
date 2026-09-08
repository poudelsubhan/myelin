import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from myelin.contracts import FeatureUnavailable, Services
from myelin.schema import GateRequest, Program, Trace

FIXTURES = Path(__file__).parent / "fixtures/contracts"


@pytest.mark.parametrize(
    "name,cls", [("program", Program), ("trace", Trace), ("gate_request", GateRequest)]
)
def test_json_roundtrip_and_unknown_fields(name, cls):
    obj = cls.model_validate_json((FIXTURES / f"{name}.json").read_text())
    assert cls.model_validate_json(obj.model_dump_json()) == obj
    with pytest.raises(ValidationError):
        cls.model_validate(obj.model_dump() | {"unexpected": "reject"})


def test_content_hash_covers_executable_fields_and_nested_ids():
    raw = json.loads((FIXTURES / "program.json").read_text())
    original = Program.model_validate(raw)
    raw["steps"][0]["url"]["value"] += "?changed=1"
    assert Program.model_validate(raw).content_hash() != original.content_hash()
    raw["steps"].append(raw["steps"][0])
    with pytest.raises(ValidationError, match="duplicate step ID"):
        Program.model_validate(raw)


def test_each_write_requires_identity_and_conditions():
    raw = json.loads((FIXTURES / "program.json").read_text())
    raw["steps"][0]["effect"] = "write"
    with pytest.raises(ValidationError, match="operation_key"):
        Program.model_validate(raw)
    raw["steps"][0]["operation_key"] = "write-1"
    raw["steps"][0]["pre"] = []
    with pytest.raises(ValidationError):
        Program.model_validate(raw)


async def test_unavailable_services_are_explicit():
    with pytest.raises(FeatureUnavailable):
        await Services().recorder("crm.create_invoice", {}, None)
    with pytest.raises(FeatureUnavailable):
        Services().session_factory()
