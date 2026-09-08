"""Immutable private registration, using safe IDs and content-addressed snapshots."""

import json
import os
from pathlib import Path

from myelin.live.schema import SiteProfile, WorkflowSpec, digest, identifier


def private_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def private_json(path, data):
    path = Path(path)
    private_directory(path.parent)
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)
    path.chmod(0o600)


class Registry:
    def __init__(self, root):
        self.root = private_directory(root)

    def put(self, value):
        kind = "sites" if isinstance(value, SiteProfile) else "workflows"
        if kind == "workflows":
            site = self.site(value.site_profile_id)
            for effect in value.effect_contract:
                if effect.request.origin not in site.api_origins + site.navigation_origins:
                    raise ValueError("effect outside site's API grants")
        path = self.root / kind / (identifier(value.id) + ".json")
        data = value.model_dump(mode="json")
        if path.exists() and json.loads(path.read_text()) != data:
            raise ValueError("registered contract is immutable; register a new ID/revision")
        private_json(path, data)
        return digest(value)

    def _get(self, kind, key, model):
        path = self.root / kind / (identifier(key) + ".json")
        if not path.is_file():
            raise ValueError(f"unknown {kind} ID")
        return model.model_validate_json(path.read_text())

    def site(self, key):
        return self._get("sites", key, SiteProfile)

    def workflow(self, key):
        return self._get("workflows", key, WorkflowSpec)

    def listing(self):
        return {
            kind: [json.loads(p.read_text()) for p in sorted((self.root / kind).glob("*.json"))]
            for kind in ("sites", "workflows")
        }
