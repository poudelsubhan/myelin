"""A frozen public-page extraction using the same site/task registry."""

from myelin.live.schema import SiteProfile, WorkflowSpec


def public_page(url, title, news_title, news_url, profile_id):
    from urllib.parse import urlsplit

    origin = "https://" + urlsplit(url).netloc

    def lit(value):
        return {"kind": "literal", "value": value}

    site = SiteProfile.model_validate(
        {
            "id": "public-python-v1",
            "start_url": url,
            "navigation_origins": [origin],
            "asset_origins": [origin],
            "auth_profile_id": profile_id,
            "capabilities": ["public_read_only"],
            "scope_locator": {
                k: {"id": k, "read": "url"} for k in ("account_id", "workspace_id", "resource_id")
            },
        }
    )
    # Public scopes pin the observed page URL, without asserting a signed-in identity.
    locator = {"strategy": "role", "role": "link", "value": news_title, "exact": True}
    spec = WorkflowSpec.model_validate(
        {
            "id": "public-news-v1",
            "revision": 1,
            "site_profile_id": site.id,
            "goal": (
                "Navigate once to the supplied page_url, then read the page title and "
                "the observed first news headline and URL. This is read-only: "
                "do not follow news links or submit any form."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"task_key": {"type": "string"}, "page_url": {"const": url}},
                "required": ["task_key", "page_url"],
                "additionalProperties": False,
            },
            "task_key_field": "task_key",
            "scope": {k: url for k in ("account_id", "workspace_id", "resource_id")},
            "marker_template": "{task_key}",
            "max_new_records": 0,
            "evidence_recipes": [
                {"id": "page_title", "read": "title"},
                {"id": "news_title", "read": "text", "locator": locator},
                {
                    "id": "resource_urls",
                    "read": "attribute",
                    "attribute_or_path": "href",
                    "locator": locator,
                    "collect_all": True,
                    "completeness": "all_rendered",
                },
            ],
            "outcome_contract": [
                {
                    "name": "page_title",
                    "recipe_id": "page_title",
                    "operator": "equals",
                    "expected": lit(title),
                },
                {
                    "name": "news_title",
                    "recipe_id": "news_title",
                    "operator": "equals",
                    "expected": lit(news_title),
                },
                {
                    "name": "news_url",
                    "recipe_id": "resource_urls",
                    "operator": "equals",
                    "expected": lit([news_url]),
                },
            ],
        }
    )
    return site, spec
