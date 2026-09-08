from pathlib import Path
from types import SimpleNamespace

import pytest

from myelin.adapters.crm import compatible, revision
from myelin.repair.reconcile import reconcile
from myelin.schema import Checkpoint, FailureContext, Program


def failure(effect="unknown"):
    return FailureContext(
        checkpoint=Checkpoint(
            run_id="r",
            step_id="write",
            completed_step_ids=["login"],
            variables={},
            secret_refs=[],
            resume_url="http://localhost:8101/form",
            observation_id="o",
            operation_id="r:write",
        ),
        request_id="request",
        error_kind="transport",
        effect_status=effect,
        expected="response",
        observed=None,
        remaining_goal="finish",
    )


@pytest.mark.parametrize(
    "status,expected", [("applied", "applied"), ("absent", "unknown"), ("pending", "unknown")]
)
async def test_reconciliation_never_retries_unknown_effects(status, expected):
    class Fake:
        settings = SimpleNamespace(crm_url="http://localhost:8101")

        async def request(self, *args):
            assert args[1] == "GET" and args[2].endswith("/api/operations/r%3Awrite")
            return {
                "status": 200,
                "body": {
                    "status": status,
                    "result": {"location": "/invoices/id"},
                    "request_hash": "known",
                },
            }

    assert (await reconcile(Fake(), failure()))["status"] == expected


async def test_definitive_prewrite_failure_does_not_query_or_repeat():
    assert (await reconcile(None, failure("not_applied")))["status"] == "not_applied"


def test_presentation_compatibility_does_not_mask_contract_changes():
    from myelin.schema import EnvironmentSpec

    program = Program.model_validate_json(Path("tests/fixtures/crm-ui-program.json").read_text())
    layout = ["reorder_fields:invoice", "move_button:mark_paid"]
    assert compatible(
        program, EnvironmentSpec(app="crm", revision=revision(layout), mutations=layout)
    )
    contract = ["rename_field:invoice_total"]
    assert not compatible(
        program, EnvironmentSpec(app="crm", revision=revision(contract), mutations=contract)
    )
