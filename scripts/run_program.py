import argparse
import asyncio
import json

from run_vision import run

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("program_hash")
    p.add_argument("--inputs")
    args = p.parse_args()
    inputs = json.loads(open(args.inputs).read()) if args.inputs else None
    result = asyncio.run(run("program", args.program_hash, inputs))
    raise SystemExit(0 if result["status"] == "completed" else 1)
