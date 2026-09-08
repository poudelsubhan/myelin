from pathlib import Path

import pytest

from myelin.program.staging import Staging
from myelin.schema import Program
from myelin.trace.store import TraceStore


def staging(tmp_path):
    p = Program.model_validate_json(Path("tests/fixtures/crm-ui-program.json").read_text())
    return Staging(tmp_path / "candidate", p, TraceStore(tmp_path / "runs", "patch"))


def test_v4a_create_update_paths_and_one_terminal_output(tmp_path):
    stage = staging(tmp_path)
    result = stage.apply(
        "create",
        {"type": "create_file", "path": "source-map.json", "diff": '+{"source": "trace"}\n'},
    )
    assert result["status"] == "completed"
    result = stage.apply(
        "update",
        {
            "type": "update_file",
            "path": "source-map.json",
            "diff": '@@\n-{"source": "trace"}\n+{"source": "verified trace"}\n',
        },
    )
    assert result["status"] == "completed"
    assert "verified trace" in (stage.root / "source-map.json").read_text()
    with pytest.raises(ValueError, match="duplicate"):
        stage.apply("update", {"type": "delete_file", "path": "source-map.json"})
    for i, path in enumerate(
        ["../program.json", "/tmp/program.json", "policy.json", "sub/../program.json"]
    ):
        assert (
            stage.apply(f"escape{i}", {"type": "create_file", "path": path, "diff": "+bad"})[
                "status"
            ]
            == "failed"
        )


def test_invalid_diff_and_schema_never_become_valid_program(tmp_path):
    stage = staging(tmp_path)
    assert (
        stage.apply(
            "bad-diff",
            {"type": "update_file", "path": "program.json", "diff": "@@\n-does not exist\n+oops"},
        )["status"]
        == "failed"
    )
    assert (
        stage.apply("delete", {"type": "delete_file", "path": "program.json"})["status"]
        == "completed"
    )
    assert (
        stage.apply(
            "invalid", {"type": "create_file", "path": "program.json", "diff": '+{"invalid":true}'}
        )["status"]
        == "completed"
    )
    with pytest.raises(ValueError):
        stage.program()
