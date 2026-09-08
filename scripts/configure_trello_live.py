"""Freeze a Trello contract from a private observed setup probe, without remote writes."""

import argparse
import json
from pathlib import Path

from myelin.config import ROOT
from myelin.live.schema import SiteProfile, WorkflowSpec
from myelin.runtime.registry import Registry, private_json


def literal(value):
    return {"kind": "literal", "value": value}


def inp(key):
    return {"kind": "input", "key": key}


def var(key):
    return {"kind": "variable", "key": key}


def definitions(probe, read_probe):
    member = read_probe[0]["body"]
    board = read_probe[1]["body"]
    lists = {row["name"]: row["id"] for row in read_probe[3]["body"]}
    board_id, workspace = board["id"], board["idOrganization"]
    board_url = (
        "https://trello.com/b/" + probe["url"].split("/b/")[-1] if "/b/" in probe["url"] else None
    )
    if not board_url:
        board_url = next(
            r["body"]["variables"]["id"]
            for r in probe["traffic"]
            if isinstance(r.get("body"), dict)
            and r["body"].get("operationName") == "TrelloCurrentBoardInfo"
        )
        board_url = "https://trello.com/b/" + board_url
    queries = sorted(
        {
            r["body"]["query"]
            for r in probe["traffic"]
            if isinstance(r.get("body"), dict)
            and r["body"].get("query", "").lstrip().startswith("query ")
        }
    )
    mutations = {
        r["body"]["operationName"]: r["body"]["query"]
        for r in probe["traffic"]
        if isinstance(r.get("body"), dict) and r["body"].get("query", "").startswith("mutation ")
    }

    def http_recipe(key, url, path):
        return {
            "id": key,
            "kind": "http",
            "navigation": literal(url),
            "read": "json_path",
            "attribute_or_path": path,
        }

    board_read = f"https://trello.com/1/boards/{board_id}?fields=id,name,idOrganization,prefs"
    site = {
        "id": "trello-sales-v1",
        "start_url": board_url,
        "navigation_origins": ["https://trello.com"],
        "api_origins": ["https://trello.com"],
        "asset_origins": [
            "https://trello.com",
            "https://trello-members.s3.amazonaws.com",
            "https://images.unsplash.com",
            "https://a.trellocdn.com",
            "https://d2k1ftgv7pobq7.cloudfront.net",
        ],
        "auth_origins": ["https://id.atlassian.com", "https://accounts.google.com"],
        "auth_profile_id": "trello",
        "sensitive_keys": ["dsc", "signature", "key"],
        "capabilities": ["ui", "press", "authenticated_get_readback"],
        "read_only_graphql_queries": queries,
        "scope_locator": {
            "account_id": http_recipe(
                "account", "https://trello.com/1/members/me?fields=id,username", "$.id"
            ),
            "workspace_id": http_recipe("workspace", board_read, "$.idOrganization"),
            "resource_id": http_recipe("board", board_read, "$.id"),
        },
    }
    cards_url = f"https://trello.com/1/boards/{board_id}/cards?filter=all&fields=id,name,desc,due,dueReminder,idBoard,idList,url,closed&checklists=all"
    recipes, assertions = [], []

    def card(key, projection, *, one=True, items=None, item_match=None, output=None):
        recipe = http_recipe(key, cards_url, "$") | {
            "select_contains": {"$.name": inp("title_marker")},
            "take_first": one,
            "project": projection,
            "completeness": "all_rendered",
        }
        if items:
            recipe["item_project"] = items
        if item_match:
            recipe["item_match"] = item_match
        if output:
            recipe.update(output_variable=output, output_index=0)
        recipes.append(recipe)

    def assertion(name, recipe, operator, expected):
        assertions.append(
            {
                "name": name,
                "recipe_id": recipe,
                "operator": operator,
                "expected": expected,
                "required": True,
            }
        )

    card("resource_urls", "$.url", one=False, output="card_url")
    assertion("one_card", "resource_urls", "unique", literal(1))
    card("resource_ids", "$.id", one=False, output="card_id")
    assertion("card_identity", "resource_ids", "unique", literal(1))
    for key, path, op, expected in [
        ("title", "$.name", "equals", inp("card_title")),
        ("list", "$.idList", "equals", inp("list_id")),
        ("card_board", "$.idBoard", "equals", literal(board_id)),
        ("description_brief", "$.desc", "contains", inp("brief")),
        ("description_marker", "$.desc", "contains", inp("description_marker")),
        ("due", "$.due", "date_equals", inp("due_iso")),
        ("reminder", "$.dueReminder", "equals", literal(-1)),
    ]:
        card(key, path)
        assertion(key, key, op, expected)
    card("checklist_names", "$.checklists", items="$.name")
    assertion("checklist_names", "checklist_names", "equals", literal(["Qualification"]))
    card("checklist_ids", "$.checklists", items="$.id", output="checklist_id")
    assertion("checklist_identity", "checklist_ids", "unique", literal(1))
    card("item_names", "$.checklists[0].checkItems", items="$.name")
    assertion(
        "item_names",
        "item_names",
        "equals",
        literal(["Review brief", "Prepare next step", "Follow up"]),
    )
    card("item_states", "$.checklists[0].checkItems", items="$.state")
    assertion("item_states", "item_states", "equals", literal(["incomplete"] * 3))
    for i, name in enumerate(["Review brief", "Prepare next step", "Follow up"]):
        key = f"item_{i}"
        card(
            key,
            "$.checklists[0].checkItems",
            items="$.name",
            item_match={"$.name": literal(name), "$.state": literal("incomplete")},
        )
        assertion(key, key, "unique", literal(1))
    base_assertions = ["one_card", "card_identity", "title", "list", "card_board"]
    effects = []

    def effect(
        key,
        method,
        path,
        body,
        names,
        *,
        dependencies=None,
        kind="update",
        marker=None,
        templates=None,
        path_bindings=None,
    ):
        request = {
            "method": method,
            "origin": "https://trello.com",
            "path_pattern": path,
            "required_body": body,
        }
        if templates:
            request["body_templates"] = templates
        if path_bindings:
            request["path_bindings"] = path_bindings
        row = {
            "key": key,
            "kind": kind,
            "target": literal(board_id) if kind == "create" else var("card_id"),
            "depends_on": dependencies or [],
            "request": request,
            "assertions": base_assertions + names,
        }
        if marker:
            row["initial_marker_field"] = marker
        effects.append(row)

    effect(
        "create_card",
        "POST",
        "/1/cards",
        {
            "name": inp("card_title"),
            "idList": inp("list_id"),
            "due": literal(None),
            "closed": literal(False),
        },
        [],
        kind="create",
        marker="name",
    )
    desc_op = "TrelloCardBackUpdateCardDescription"
    effect(
        "set_description",
        "POST",
        "/gateway/api/graphql",
        {
            "operationName": literal(desc_op),
            "query": literal(mutations[desc_op]),
        },
        ["description_brief", "description_marker"],
        dependencies=["create_card"],
        templates={
            "$.variables.cardId": f"ari:cloud:trello::card/workspace/{workspace}/{{card_id}}"
        },
    )
    effects[-1]["request"]["body_contains"] = {
        "$.variables.description": [inp("brief"), inp("description_marker")]
    }
    due_op = "TrelloCardBackUpdateCardDates"
    effect(
        "set_due",
        "POST",
        "/gateway/api/graphql",
        {
            "operationName": literal(due_op),
            "query": literal(mutations[due_op]),
            "$.variables.dueAt": inp("due_utc"),
            "$.variables.dueReminder": literal(-1),
        },
        ["due", "reminder"],
        dependencies=["create_card"],
        templates={"$.variables.cardId": "ari:cloud:trello::card/workspace/none/{card_id}"},
    )
    effect(
        "create_checklist",
        "POST",
        "/1/checklists",
        {"idBoard": literal(board_id), "idCard": var("card_id"), "name": literal("Qualification")},
        ["checklist_names", "checklist_identity"],
        dependencies=["create_card"],
    )
    for i, name in enumerate(["Review brief", "Prepare next step", "Follow up"]):
        effect(
            f"add_item:{i}",
            "POST",
            r"/1/cards/(?P<card>[a-f0-9]+)/checklist/(?P<checklist>[a-f0-9]+)/checkItem",
            {"name": literal(name), "due": literal(None)},
            ["checklist_names", "checklist_identity", f"item_{i}"],
            dependencies=["create_checklist"] + ([f"add_item:{i - 1}"] if i else []),
            path_bindings={"card": var("card_id"), "checklist": var("checklist_id")},
        )
    fields = {
        k: {"type": "string", "minLength": 1}
        for k in [
            "task_key",
            "lead_name",
            "company",
            "brief",
            "target_list",
            "follow_up_date",
            "timezone",
        ]
    }
    fields["target_list"]["enum"] = list(lists)
    fields["timezone"]["const"] = "America/Los_Angeles"
    derived = []

    def bind(target, operation, *values):
        derived.append({"target": target, "operation": operation, "values": list(values)})

    bind("title_marker", "concat", literal("["), inp("task_key"), literal("]"))
    bind(
        "card_title",
        "concat",
        inp("lead_name"),
        literal(" — "),
        inp("company"),
        literal(" "),
        inp("title_marker"),
    )
    bind("description_marker", "concat", literal("Myelin task: "), inp("task_key"))
    bind("description", "concat", inp("brief"), literal("\n\n"), inp("description_marker"))
    bind("list_id", "lookup", literal(lists), inp("target_list"))
    bind("add_card_label", "concat", literal("Add a card in "), inp("target_list"))
    bind("save_card_label", "concat", literal("Add card in "), inp("target_list"))
    bind("due_iso", "local_datetime", inp("follow_up_date"), literal("09:00"), inp("timezone"))
    bind(
        "due_utc",
        "format_datetime",
        inp("due_iso"),
        literal("%Y-%m-%dT%H:%M:%S.000Z"),
        literal("UTC"),
    )
    bind("due_ui_date", "format_datetime", inp("due_iso"), literal("%-m/%-d/%Y"), inp("timezone"))
    bind("due_ui_time", "copy", literal("9:00 AM"))
    spec = {
        "id": "sales-follow-up-v3",
        "revision": 3,
        "site_profile_id": site["id"],
        "goal": (
            "Create exactly one sales follow-up card on the connected Myelin Sales Operations "
            "board for the supplied example lead. Use card_title as its title, the exact "
            "brief and description_marker in its description, target_list, and the requested "
            "follow-up date at 9:00 AM America/Los_Angeles with no reminder. Add exactly one "
            "Qualification checklist containing Review brief, Prepare next step, Follow up, "
            "all unchecked. These are labelled example leads; do not send outreach or invite "
            "anyone. Discover the actions from the visible page. "
        ),
        "input_schema": {
            "type": "object",
            "properties": fields,
            "required": list(fields),
            "additionalProperties": False,
        },
        "derived_fields": derived,
        "task_key_field": "task_key",
        "scope": {"account_id": member["id"], "workspace_id": workspace, "resource_id": board_id},
        "marker_template": "Myelin task: {task_key}",
        "outcome_contract": assertions,
        "evidence_recipes": recipes,
        "allowed_effects": [e["key"] for e in effects],
        "effect_contract": effects,
        "max_new_records": 1,
    }
    return SiteProfile.model_validate(site), WorkflowSpec.model_validate(spec)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--reads", type=Path, required=True)
    args = parser.parse_args()
    site, spec = definitions(json.loads(args.probe.read_text()), json.loads(args.reads.read_text()))
    registry = Registry(ROOT / ".local/myelin")
    registry.put(site)
    registry.put(spec)
    names = [
        ("Jordan Lee", "Northwind Studio"),
        ("Taylor Chen", "Cedar Analytics"),
        ("Morgan Patel", "Harbor Design"),
        ("Alex Rivera", "Brightfield Labs"),
        ("Casey Brooks", "Maple Operations"),
        ("Jamie Park", "Summit Works"),
        ("Riley Quinn", "Elm Creative"),
        ("Avery Shaw", "Pine Systems"),
    ]
    rows = [
        {
            "task_key": f"lead-demo-{i + 1:03}",
            "lead_name": name,
            "company": company,
            "brief": f"Example lead: {name} at {company} requested a product walkthrough "
            "for a five-person operations team.",
            "target_list": "Qualified",
            "follow_up_date": "2026-09-09",
            "timezone": "America/Los_Angeles",
        }
        for i, (name, company) in enumerate(names)
    ]
    private_json(
        ROOT / ".local/myelin/manifest.json",
        {
            "workflow_id": spec.id,
            "profile_id": "trello",
            "learn": rows[0],
            "canaries": rows[1:3],
            "batch": rows[3:],
        },
    )
    print("Registered", site.id, spec.id, "with one learn, two canaries and five batch rows.")


if __name__ == "__main__":
    main()
