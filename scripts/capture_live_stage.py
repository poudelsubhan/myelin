"""Capture one fixed, journaled final replay and fresh read-back; never generate new keys."""

import asyncio
import json
import time

from myelin.browser.session import Session
from myelin.config import ROOT, Settings
from myelin.live.orchestration import LiveOrchestrator
from myelin.runtime.registry import private_json

WORK = ROOT / ".local/myelin/video"


class FilmedSession(Session):
    async def open(self, *args):
        await super().open(*args)
        await self.page.set_viewport_size({"width": 1440, "height": 900})
        self.film_active = True
        self.frames = []
        self.film_start = time.monotonic()

        async def capture():
            while self.film_active:
                path = WORK / "execution" / f"{len(self.frames):05}.png"
                try:
                    await self.page.screenshot(path=str(path))
                    self.frames.append(
                        {"path": str(path), "time": time.monotonic() - self.film_start}
                    )
                except Exception:
                    break
                await asyncio.sleep(0.12)

        self.film_task = asyncio.create_task(capture())

    async def close(self):
        if hasattr(self, "film_task"):
            await asyncio.sleep(1)
            self.film_active = False
            await self.film_task
            private_json(WORK / "execution-frames.json", self.frames)
        await super().close()


async def main():
    (WORK / "execution").mkdir(parents=True, exist_ok=True)
    root = ROOT / ".local/myelin"
    manifest = json.loads((root / "manifest.json").read_text())
    engine = LiveOrchestrator(Settings.load(), root)
    try:
        result = await engine.readback(
            manifest["workflow_id"],
            manifest["profile_id"],
            [manifest["learn"]] + manifest["canaries"] + manifest["batch"],
        )
        private_json(WORK / "fresh-readback.json", result)
        assert result["status"] == "verified", result
        engine.session_factory = FilmedSession
        row = dict(manifest["learn"])
        row.update(
            task_key="stage-demo-001",
            lead_name="Stage Example",
            company="Myelin Demo",
            brief=(
                "Example stage follow-up: demonstrate a verified operations walkthrough "
                "with no outreach."
            ),
        )
        result = await engine.run(manifest["workflow_id"], manifest["profile_id"], row)
        if result["reused"]:
            if (WORK / "stage-result.json").exists():
                print("Existing verified stage footage retained; no new card created.")
                return
            raise ValueError("Stage task already exists; no captured footage is available")
        private_json(WORK / "stage-result.json", result)
        assert result["status"] == "verified" and result["model_calls"] == 0, result
        print(
            "FINAL REPLAY VERIFIED",
            result["run_id"],
            result["evidence"]["resource_urls"],
            flush=True,
        )
    finally:
        await engine.profiles.close()


if __name__ == "__main__":
    asyncio.run(main())
