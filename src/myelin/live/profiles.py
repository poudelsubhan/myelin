"""Private dedicated profiles or an explicitly approved everyday Chrome connection."""

import fcntl
import json
from pathlib import Path

from playwright.async_api import async_playwright

from myelin.live.schema import https_origin, identifier
from myelin.runtime.registry import private_directory, private_json


class ProfileManager:
    def __init__(self, root):
        self.root = private_directory(root)
        self.connections = {}
        self.attached = {}

    def use_everyday_chrome(self, profile_id):
        private_json(self.folder(profile_id) / "mode.json", {"mode": "existing_chrome"})

    def is_existing(self, profile_id):
        path = self.folder(profile_id) / "mode.json"
        return path.exists() and json.loads(path.read_text()).get("mode") == "existing_chrome"

    async def existing_browser(self, profile_id):
        if profile_id in self.attached:
            pw, browser = self.attached[profile_id]
            if browser.is_connected():
                return browser
            await pw.stop()
            del self.attached[profile_id]
        # Chrome exposes connection metadata here after the user explicitly enables
        # remote debugging. We do not read/copy any daily-profile credential stores.
        bridge = self.root.parent / "chrome-bridge.json"
        if bridge.exists():
            endpoint = json.loads(bridge.read_text())["endpoint"]
            if not endpoint.startswith("ws://127.0.0.1:"):
                raise ValueError("Chrome bridge must be local")
        else:
            path = Path.home() / "Library/Application Support/Google/Chrome/DevToolsActivePort"
            port, target = path.read_text().splitlines()[:2]
            if not port.isdigit() or not target.startswith("/devtools/browser/"):
                raise ValueError("invalid local Chrome debugging endpoint")
            endpoint = f"ws://127.0.0.1:{port}{target}"
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.connect_over_cdp(endpoint)
        except BaseException:
            await pw.stop()
            raise
        self.attached[profile_id] = (pw, browser)
        return browser

    def folder(self, profile_id):
        return private_directory(self.root / identifier(profile_id))

    def acquire(self, profile_id):
        folder = self.folder(profile_id)
        handle = (folder / "owner.lock").open("a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise ValueError("profile already open; finish the authentication connection") from None
        return handle

    async def connect(self, profile_id, url):
        https_origin(url)
        if profile_id in self.connections:
            return {"status": "needs_user_authentication", "profile_id": profile_id}
        lock = self.acquire(profile_id)
        pw = await async_playwright().start()
        try:
            context = await pw.chromium.launch_persistent_context(
                str(self.folder(profile_id) / "browser"),
                headless=False,
                channel="chrome",
                viewport={"width": 1440, "height": 1000},
            )
            dependencies = set()
            # Only dependency origins are observed in connect mode. No cookies,
            # credentials, request bodies, screenshots or login DOM are exported.
            context.on(
                "request",
                lambda req: (
                    dependencies.add(https_origin(req.url))
                    if req.url.startswith("https://")
                    else None
                ),
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            self.connections[profile_id] = (pw, context, lock, dependencies)
            return {"status": "needs_user_authentication", "profile_id": profile_id}
        except BaseException:
            await pw.stop()
            lock.close()
            raise

    async def finish(self, profile_id):
        if profile_id not in self.connections:
            raise ValueError("profile has no open connection")
        pw, context, lock, dependencies = self.connections.pop(profile_id)
        try:
            urls = [page.url.split("?")[0].split("#")[0] for page in context.pages]
            data = {
                "status": "connected_unverified",
                "profile_id": profile_id,
                "page_urls": urls,
                "observed_origins": sorted(dependencies),
            }
            private_json(self.folder(profile_id) / "connection.json", data)
            await context.close()
            return data
        finally:
            await pw.stop()
            lock.close()
            for path in self.folder(profile_id).rglob("*"):
                if not path.is_symlink():
                    path.chmod(0o700 if path.is_dir() else 0o600)

    async def close(self):
        for key in list(self.connections):
            await self.finish(key)
        for pw, _ in self.attached.values():
            # Disconnect only. Never close the user's everyday browser.
            await pw.stop()
        self.attached.clear()
