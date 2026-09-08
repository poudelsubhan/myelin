"""Declared SPA bearer bridge. Authentication is always fresh per run."""

import hashlib
import json

MUTATIONS = {"rename_field:amount", "add_step:confirm_submit", "throttle:list"}
STORAGE_KEY = "myelin_expense_token"


def revision(mutations):
    if not mutations:
        return "expense-v1"
    return "expense-v1-" + hashlib.sha256(json.dumps(sorted(mutations)).encode()).hexdigest()[:12]


async def resume(session, url):
    token = session.secrets.get("bearer_token")
    if token:
        # Origin is validated by session routing; set only the declared app storage key.
        await session.page.goto(session.settings.crm_url)
        await session.page.evaluate(
            "([key, token]) => localStorage.setItem(key, token)", [STORAGE_KEY, token]
        )
    return url
