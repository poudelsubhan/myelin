"""V4A edits can change only candidate files in a dedicated staging directory."""

from uuid import uuid4

from agents.apply_diff import apply_diff

from myelin.schema import Program


class Staging:
    ALLOWED = {"program.json", "source-map.json"}

    def __init__(self, root, program, store):
        self.root, self.store = root.resolve(), store
        self.root.mkdir(parents=True, exist_ok=False)
        (self.root / "program.json").write_text(program.model_dump_json(indent=2) + "\n")
        self.calls = set()

    def apply(self, call_id, operation):
        if call_id in self.calls:
            raise ValueError("duplicate apply_patch call ID")
        self.calls.add(call_id)
        status = "failed"
        try:
            name = operation["path"]
            if name not in self.ALLOWED:
                raise ValueError("path outside candidate allowlist")
            path = self.root / name
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise ValueError("symlink or escaped candidate path")
            kind = operation["type"]
            if kind == "delete_file":
                path.unlink()
            else:
                diff = operation["diff"]
                if not isinstance(diff, str) or len(diff.encode()) > 1_000_000:
                    raise ValueError("invalid or oversized diff")
                if kind == "create_file":
                    if path.exists():
                        raise ValueError("file already exists")
                    text = apply_diff("", diff, mode="create")
                elif kind == "update_file":
                    text = apply_diff(path.read_text(), diff)
                else:
                    raise ValueError("unknown patch operation")
                if len(text.encode()) > 1_000_000:
                    raise ValueError("oversized candidate")
                temp = self.root / (".patch-" + str(uuid4()))
                temp.write_text(text)
                temp.replace(path)
            status, output = "completed", f"{kind} {name}; candidate validation still required"
        except (ValueError, KeyError, OSError) as exc:
            output = str(exc)[:500]
        result = {
            "type": "apply_patch_call_output",
            "call_id": call_id,
            "status": status,
            "output": output,
        }
        self.store.append("patches.jsonl", {"operation": operation, "result": result})
        return result

    def program(self):
        return Program.model_validate_json((self.root / "program.json").read_text())
