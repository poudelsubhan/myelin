import argparse
import json
from pathlib import Path

from myelin.metrics import aggregate
from myelin.schema import UsageRecord

parser = argparse.ArgumentParser()
parser.add_argument("run_id")
args = parser.parse_args()
path = Path("runs") / args.run_id / "usage.jsonl"
records = [UsageRecord.model_validate_json(line) for line in path.read_text().splitlines()]
print(json.dumps(aggregate(records), indent=2))
