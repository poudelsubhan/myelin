import hashlib
import json
import re
import time
from pathlib import Path
from uuid import uuid4

from myelin.schema import Observation
from myelin.trace.sanitize import Sanitizer


class TraceStore:
    def __init__(self, root: Path, run_id: str, sanitizer: Sanitizer | None = None):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            raise ValueError("invalid run ID")
        self.folder = root / run_id
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "observations").mkdir(exist_ok=True)
        self.sanitizer = sanitizer or Sanitizer()

    def append(self, name, value):
        data = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        with (self.folder / name).open("a") as f:
            f.write(json.dumps(self.sanitizer.clean(data), ensure_ascii=False) + "\n")
            f.flush()

    def save(self, name, value):
        data = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        target = self.folder / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(self.sanitizer.clean(data), indent=2, ensure_ascii=False))
        temporary.replace(target)

    def observation(self, raw, action_id=None):
        oid = str(uuid4())
        prefix = f"observations/{oid}"
        aria = self.sanitizer.clean(raw.aria)
        (self.folder / f"{prefix}.png").write_bytes(raw.screenshot)
        (self.folder / f"{prefix}.txt").write_text(aria)
        obs = Observation(
            id=oid,
            action_id=action_id,
            timestamp=time.time(),
            url=self.sanitizer.url(raw.url),
            frame_path=f"{prefix}.png",
            aria_path=f"{prefix}.txt",
            aria_hash=hashlib.sha256(aria.encode()).hexdigest(),
        )
        self.append("observations.jsonl", obs)
        return obs
