from scripts.demo import INPUT, NINTH


def test_ninth_input_is_distinct_from_locked_cases():
    from myelin.gate.gate import suite

    assert NINTH != INPUT
    assert NINTH not in [c.inputs for c in suite()]


def test_repair_preserves_unaffected_model_compiled_steps():
    from pathlib import Path

    from myelin.schema import Program

    root = Path("programs/crm.create_invoice")
    before = Program.model_validate_json(
        (
            root
            / "6f83a7afde1d4e02b2e67826242f22557bcc9d986cafb420520724310fd04187"
            / "program.json"
        ).read_text()
    )
    after = Program.model_validate_json(
        (
            root
            / "940f61ecd6088081b98cb361389598f87c4d57a969dd2959b4e84fcd0303ad97"
            / "program.json"
        ).read_text()
    )
    assert [s.id for s in before.steps] == [s.id for s in after.steps]
    for old, new in zip(before.steps, after.steps, strict=True):
        if old.id != "invoice-write":
            assert old == new
    old = next(s for s in before.steps if s.id == "invoice-write")
    new = next(s for s in after.steps if s.id == "invoice-write")
    assert "total_cents" in old.body and "amount_due_cents" in new.body
    assert new.body["amount_due_cents"] == old.body["total_cents"]


def test_costlier_fixture_keeps_operation_identities_unique():
    from pathlib import Path

    from myelin.schema import Program
    from scripts.demo import with_redundant_fill

    program = Program.model_validate_json(Path("tests/fixtures/crm-ui-program.json").read_text())
    program.steps[1].effect = "write"
    program.steps[1].operation_key = "email-original"
    candidate = with_redundant_fill(program)
    assert len(candidate.steps) == len(program.steps) + 1
    assert candidate.steps[2].operation_key == "redundant-fill"
    assert program.steps[1].operation_key == "email-original"
    Program.model_validate_json(candidate.model_dump_json())
