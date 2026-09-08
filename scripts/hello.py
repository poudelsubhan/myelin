"""Phase 0 real browser and Responses probe; unsupported access never passes."""

import argparse
import asyncio
import base64
import json
import time
from uuid import uuid4

from openai import AsyncOpenAI
from playwright.async_api import async_playwright

from myelin.config import Settings


async def probe(browser_only=False):
    settings = Settings.load()
    run_id = f"smoke-{uuid4()}"
    folder = settings.runs_dir / run_id
    folder.mkdir(parents=True)
    evidence = {
        "run_id": run_id,
        "timestamp": time.time(),
        "model": settings.model,
        "browser": "pending",
        "responses": "not_requested",
        "passed": False,
    }
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=settings.headless)
            try:
                page = await browser.new_page()
                await page.goto(f"{settings.crm_url}/login", timeout=15000)
                await page.get_by_role("heading", name="Sign in").wait_for()
                screenshot = await page.screenshot(path=str(folder / "browser.png"))
                evidence["browser"] = "passed"
            finally:
                await browser.close()
        if browser_only:
            evidence["responses"] = "skipped_browser_only"
            return evidence
        if not settings.live or not settings.api_key:
            evidence["responses"] = "blocked_missing_key_or_live_flag"
            return evidence
        async with AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout_s,
            max_retries=0,
        ) as client:
            response = await client.responses.create(
                model=settings.model,
                reasoning={"effort": "low"},
                max_output_tokens=256,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Read the screenshot. Reply with the page heading.",
                            },
                            {
                                "type": "input_image",
                                "image_url": "data:image/png;base64,"
                                + base64.b64encode(screenshot).decode(),
                            },
                        ],
                    }
                ],
            )
        evidence.update(
            response_id=response.id,
            response_status=response.status,
            usage=response.usage.model_dump() if response.usage else None,
        )
        evidence["responses"] = (
            "passed"
            if (response.status == "completed" and "sign in" in response.output_text.lower())
            else "failed"
        )
        evidence["passed"] = evidence["responses"] == "passed"
    except Exception as exc:
        # Exceptions can contain credential-bearing request data; retain only safe classification.
        evidence["error_type"] = type(exc).__name__
        evidence["http_status"] = getattr(exc, "status_code", None)
    finally:
        (folder / "capabilities.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps(evidence, indent=2))
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-only", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(probe(args.browser_only))
    raise SystemExit(
        0 if result["passed"] or (args.browser_only and result["browser"] == "passed") else 1
    )
