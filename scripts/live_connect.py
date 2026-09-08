"""Open a dedicated private login window; no model calls or login trace."""

import argparse
import asyncio

from myelin.config import ROOT
from myelin.live.profiles import ProfileManager


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="trello")
    parser.add_argument("--url", default="https://trello.com/login")
    args = parser.parse_args()
    manager = ProfileManager(ROOT / ".local/myelin/auth")
    await manager.connect(args.profile, args.url)
    print(
        "Sign in in the Myelin browser. Open the intended board, then press Enter here.", flush=True
    )
    try:
        await asyncio.to_thread(input)
        result = await manager.finish(args.profile)
        print("Private profile saved. Identity still requires verification.", flush=True)
        print("Board pages:", result["page_urls"], flush=True)
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
