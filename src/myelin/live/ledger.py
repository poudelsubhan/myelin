"""Account/workspace/spec-scoped immutable candidates and bounded canary promotion."""

import json

from myelin.live.schema import LiveProgramBinding, digest, identifier
from myelin.runtime.registry import private_directory, private_json
from myelin.schema import Program


class LiveLedger:
    def __init__(self, root, journal):
        self.root, self.journal = private_directory(root), journal

    @staticmethod
    def scope(spec, site):
        return digest(
            {
                "spec": digest(spec),
                "site": digest(site),
                "scope": spec.scope,
                "auth_profile_id": site.auth_profile_id,
            }
        )

    def put(self, program, binding, learned_task):
        hashed = program.content_hash()
        if hashed != binding.candidate_hash:
            raise ValueError("candidate binding hash mismatch")
        folder = private_directory(self.root / "candidates" / hashed)
        path = folder / "candidate.json"
        data = {
            "program": program.model_dump(mode="json"),
            "binding": binding.model_dump(mode="json"),
            "learned_task": learned_task,
        }
        if path.exists() and json.loads(path.read_text()) != data:
            raise ValueError("immutable candidate collision")
        private_json(path, data)
        return hashed

    def get(self, candidate_hash):
        if len(candidate_hash) != 64 or any(c not in "0123456789abcdef" for c in candidate_hash):
            raise ValueError("invalid candidate hash")
        path = self.root / "candidates" / candidate_hash / "candidate.json"
        if not path.exists():
            raise ValueError("unknown live candidate")
        data = json.loads(path.read_text())
        program = Program.model_validate(data["program"])
        binding = LiveProgramBinding.model_validate(data["binding"])
        if program.content_hash() != candidate_hash or binding.candidate_hash != candidate_hash:
            raise ValueError("immutable candidate corruption")
        return program, binding, data["learned_task"]

    def current(self, scope):
        path = self.root / "current" / (identifier(scope) + ".json")
        return json.loads(path.read_text())["candidate_hash"] if path.exists() else None

    def promote(self, scope, candidate_hash, canaries, expected_parent):
        _, binding, learned_task = self.get(candidate_hash)
        tasks = [r["task_key"] for r in canaries]
        if len(tasks) != 2 or len(set(tasks)) != 2 or learned_task in tasks:
            raise ValueError("two distinct new canaries required; learning is never replayed")
        for run in canaries:
            if (
                run.get("status") != "verified"
                or run.get("candidate_hash") != candidate_hash
                or run.get("scope_hash") != scope
                or run.get("spec_hash") != binding.spec_hash
                or run.get("model_calls") != 0
                or run.get("reused")
            ):
                raise ValueError("canary evidence does not prove this candidate executed")
        with self.journal.lease("promotion:" + scope):
            if self.current(scope) != expected_parent:
                raise ValueError("current pointer changed during validation")
            data = {
                "candidate_hash": candidate_hash,
                "state": "current",
                "previous": expected_parent,
                "canaries": canaries,
            }
            private_json(self.root / "history" / (digest(data) + ".json"), data)
            private_json(self.root / "current" / (scope + ".json"), data)
