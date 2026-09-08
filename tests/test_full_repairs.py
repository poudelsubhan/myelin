from pathlib import Path

import pytest

from myelin.program.bindings import BindingError
from myelin.program.repair_scope import validate_scope
from myelin.schema import Program


def test_ui_repair_can_expand_only_failed_window_and_preserves_final_assertions():
    prior = Program.model_validate_json(Path("tests/fixtures/crm-ui-program.json").read_text())
    candidate = prior.model_copy(deep=True)
    extra = candidate.steps[-1].model_copy(deep=True)
    extra.id = "new-consent-action"
    extra.operation_key = "consent-action"
    candidate.steps.append(extra)
    validate_scope(candidate, prior, [prior.steps[-1].id])
    candidate.steps[0].intent = "unrelated change"
    with pytest.raises(BindingError, match="unaffected"):
        validate_scope(candidate, prior, [prior.steps[-1].id])
    candidate = prior.model_copy(deep=True)
    candidate.final_post[0].op = "ne"
    with pytest.raises(BindingError, match="final assertions"):
        validate_scope(candidate, prior, [prior.steps[-1].id])
