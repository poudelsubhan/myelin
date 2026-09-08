"""Only the explicit, unambiguous demo rule selects the locked manager policy."""

from myelin.schema import Compare, LiteralRef

TEXT = "when the expense is over $500, add manager note 'approved by demo'."
REVISION = "expense-policy-manager-v2"


def parse(text):
    if text.strip().lower().rstrip(".") != TEXT.rstrip("."):
        raise ValueError("This demo recognizes the explicit over-$500 approval-note rule only")
    return Compare(source="input", path="amount_cents", op="gt", expected=LiteralRef(value=50000))
