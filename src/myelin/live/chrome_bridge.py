"""Keep one explicitly approved Chrome CDP connection alive across server reloads.

The loopback WebSocket rejects browser Origins and requires a private random path.
It accepts one Myelin client at a time and never stores messages or credentials.
"""

import argparse
import asyncio
import json
import secrets
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from myelin.runtime.registry import private_json


async def run(endpoint, state_path):
    token = secrets.token_urlsafe(32)
    pending, sessions = {}, set()
    client = None
    sequence = 0
    lock = asyncio.Lock()
    async with connect(endpoint, max_size=None) as upstream:

        async def send(data, downstream_id=None):
            nonlocal sequence
            sequence += 1
            if downstream_id is not None:
                pending[sequence] = (downstream_id, data.get("method"))
            await upstream.send(json.dumps(data | {"id": sequence}))

        async def receive():
            async for raw in upstream:
                data = json.loads(raw)
                if client is None and data.get("method") == "Target.attachedToTarget":
                    sid = data["params"]["sessionId"]
                    await send({"method": "Runtime.runIfWaitingForDebugger", "sessionId": sid})
                    await send({"method": "Target.detachFromTarget", "params": {"sessionId": sid}})
                    continue
                if "id" in data:
                    found = pending.pop(data["id"], None)
                    if found is None:
                        continue
                    old_id, method = found
                    data["id"] = old_id
                    if method in ("Target.attachToTarget", "Target.attachToBrowserTarget"):
                        sid = data.get("result", {}).get("sessionId")
                        if sid:
                            sessions.add(sid)
                if client is not None:
                    try:
                        await client.send(json.dumps(data))
                    except Exception:
                        pass

        async def handler(socket):
            nonlocal client
            if socket.request.path != "/" + token or socket.request.headers.get("Origin"):
                await socket.close(1008, "private local client required")
                return
            if lock.locked():
                await socket.close(1013, "Myelin connection already in use")
                return
            async with lock:
                client = socket
                try:
                    async for raw in socket:
                        data = json.loads(raw)
                        if data.get("method") == "Browser.close":
                            await socket.send(
                                json.dumps(
                                    {
                                        "id": data["id"],
                                        "error": {
                                            "code": -32000,
                                            "message": "Closing everyday Chrome is prohibited",
                                        },
                                    }
                                )
                            )
                            continue
                        await send(data, data["id"])
                finally:
                    client = None
                    pending.clear()
                    await send({"method": "Target.setAutoAttach", "params": {
                        "autoAttach": False, "waitForDebuggerOnStart": False, "flatten": True}})
                    for sid in list(sessions):
                        await send({"method": "Runtime.runIfWaitingForDebugger", "sessionId": sid})
                        await send(
                            {"method": "Target.detachFromTarget", "params": {"sessionId": sid}}
                        )
                    sessions.clear()

        receiver = asyncio.create_task(receive())
        async with serve(handler, "127.0.0.1", 0, max_size=None) as server:
            port = server.sockets[0].getsockname()[1]
            private_json(state_path, {"endpoint": f"ws://127.0.0.1:{port}/{token}"})
            await receiver


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--minutes", type=int, default=60, choices=range(1, 61))
    args = parser.parse_args()
    path = Path.home() / "Library/Application Support/Google/Chrome/DevToolsActivePort"
    port, target = path.read_text().splitlines()[:2]
    if not port.isdigit() or not target.startswith("/devtools/browser/"):
        raise ValueError("invalid Chrome endpoint")
    try:

        async def bounded():
            async with asyncio.timeout(args.minutes * 60):
                await run(f"ws://127.0.0.1:{port}{target}", args.state)

        asyncio.run(bounded())
    except TimeoutError:
        pass
    finally:
        args.state.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
