import hashlib
import json

PRESENTATION = {"reorder_fields:invoice", "move_button:mark_paid"}


def revision(mutations):
    if not mutations:
        return "crm-v1"
    return "crm-v1-" + hashlib.sha256(json.dumps(sorted(mutations)).encode()).hexdigest()[:12]


def compatible(program, environment):
    if environment.revision in program.supported_revisions:
        return True
    contract_revision = revision(set(environment.mutations) - PRESENTATION)
    return contract_revision in program.supported_revisions
