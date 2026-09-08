# ruff: noqa: E501
"""Capture the actual console and its recorded evidence; replay only existing batch keys."""

import asyncio
import json

from playwright.async_api import async_playwright

from myelin.config import ROOT
from myelin.runtime.registry import private_json


async def main():
    work = ROOT / ".local/myelin/video"
    work.mkdir(exist_ok=True)
    endpoint = json.loads((ROOT / ".local/myelin/chrome-bridge.json").read_text())["endpoint"]
    frames = {}
    errors = []
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(endpoint)
        page = await browser.contexts[0].new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.set_viewport_size({"width": 1440, "height": 900})

        async def shot(name):
            await asyncio.sleep(0.35)
            path = work / (name + ".png")
            await page.screenshot(path=str(path))
            frames[name] = str(path)

        try:
            await page.goto("http://127.0.0.1:8104/live")
            await page.wait_for_function(
                "document.getElementById('status').textContent==='Saved verified results'"
            )
            await shot("console")
            await page.get_by_role("button", name="Five-row batch", exact=True).click()
            await page.locator("#inputs").scroll_into_view_if_needed()
            await shot("batch-inputs")
            await page.get_by_role("button", name="Run batch", exact=True).click()
            await page.wait_for_function(
                "document.getElementById('status').textContent==='verified'"
            )
            await page.locator("#result-panel").scroll_into_view_if_needed()
            await shot("batch-results")
            assert await page.locator("#results .row").count() == 5
            assert "0 model calls" in await page.locator("#results").inner_text()
            await page.get_by_role("button", name="Recorded Astra learning", exact=True).click()
            for i in range(4):
                await page.locator("#replay-image").evaluate("(img)=>img.decode()")
                await shot("learning-" + str(i))
                for _ in range(3):
                    await page.get_by_role(
                        "button", name="Next recorded action", exact=True
                    ).click()
            await page.locator("#component").select_option("effects")
            await page.get_by_role("button", name="View implementation", exact=True).click()
            await page.locator("#inspect-text").evaluate(
                "el=>{el.style.fontSize='17px';el.scrollTop=el.textContent.slice(0,el.textContent.indexOf('self.journal.dispatch')).split('\\n').length*25-70}"
            )
            await shot("effects-code")
            await page.locator("#component").select_option("repair")
            await page.get_by_role("button", name="View implementation", exact=True).click()
            await page.locator("#inspect-text").evaluate(
                "el=>el.scrollTop=el.textContent.slice(0,el.textContent.indexOf('response = await astra.respond')).split('\\n').length*25-50"
            )
            await shot("repair-code")
            await page.locator("#component").select_option("async")
            await page.get_by_role("button", name="View implementation", exact=True).click()
            await shot("async-code")
            await page.locator("#component").select_option("steering")
            await page.get_by_role("button", name="View implementation", exact=True).click()
            await shot("steering-code")
            assert not errors, errors
            private_json(work / "console-frames.json", frames)
            print("CONSOLE CAPTURE PASS; five existing rows replayed; no JavaScript errors")
        finally:
            await page.close()


if __name__ == "__main__":
    asyncio.run(main())
