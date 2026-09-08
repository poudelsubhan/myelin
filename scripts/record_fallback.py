"""Record real console evidence with an explicit replay label; no simulated model calls."""

import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def record(evidence_path):
    evidence = json.loads(Path(evidence_path).read_text())
    folder = Path(evidence_path).parent / "recorded-fallback"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=str(folder),
            record_video_size={"width": 1280, "height": 720},
        )
        page = await context.new_page()
        await page.goto("http://localhost:8100")
        for label, row in evidence["runs"].items():
            await page.evaluate(
                """([id,label]) => {
                    follow(id);
                    let banner=document.getElementById('replay-label');
                    if(!banner){
                        banner=document.createElement('div');
                        banner.id='replay-label';
                        banner.style='position:fixed;bottom:0;left:0;right:0;padding:14px;'
                            +'background:#f3d47b;color:#18251a;z-index:1000;font:16px system-ui';
                        document.body.append(banner);
                    }
                    banner.textContent='RECORDED REPLAY · '+label+' · original run '+id;
                }""",
                [row["run_id"], label],
            )
            await page.wait_for_function(
                "document.getElementById('status').textContent==='completed'"
            )
            await page.wait_for_timeout(5000)
        await context.close()
        path = await page.video.path()
        (folder / "manifest.json").write_text(
            json.dumps(
                {
                    "type": "recorded_replay",
                    "demo_id": evidence["demo_id"],
                    "source_runs": {k: v["run_id"] for k, v in evidence["runs"].items()},
                    "video": str(path),
                },
                indent=2,
            )
        )
        print(path)
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence")
    asyncio.run(record(parser.parse_args().evidence))
