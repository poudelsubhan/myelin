"""A second task is configuration only; no runtime task-name dispatch."""

import json

from myelin.live.schema import WorkflowSpec


def onboarding(base):
    data = base.model_dump(mode="json")
    raw = json.dumps(data).replace('"lead_name"', '"customer_name"')
    for old, new in [
        ("Qualification", "Onboarding"),
        ("Review brief", "Confirm goals"),
        ("Prepare next step", "Schedule kickoff"),
        ("Follow up", "Share next steps"),
    ]:
        raw = raw.replace(old, new)
    data = json.loads(raw)
    data.update(
        id="customer-onboarding-v1",
        revision=1,
        goal=(
            "Create exactly one example customer onboarding card in target_list "
            "on the connected board. "
            "Use card_title and the exact supplied brief plus description_marker. "
            "Set the requested "
            "card date at 9 AM America/Los_Angeles, no reminder. Add one Onboarding checklist with "
            "Confirm goals, Schedule kickoff, Share next steps, all unchecked. Discover visible "
            "controls. Do not send messages, invite people, or perform any other work."
        ),
    )
    return WorkflowSpec.model_validate(data)
