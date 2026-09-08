"""Branch coverage is derived from actual execution artifacts, never inferred as passed."""

import json

from myelin.schema import Branch, Compare, LiteralRef


def branch_coverage(program, cases, runs_root):
    branches = {}

    def visit(steps):
        for step in steps:
            if isinstance(step, Branch):
                branches[step.id] = step
                visit(step.then)
                visit(step.otherwise)

    visit(program.steps)
    result = {sid: {"true": [], "false": [], "boundary": []} for sid in branches}
    for case in cases:
        path = runs_root / case.program_run_id / "branches.jsonl"
        if not path.exists():
            continue
        for row in map(json.loads, path.read_text().splitlines()):
            sid = row["step_id"]
            if sid not in branches:
                raise ValueError("unknown executed branch")
            result[sid]["true" if row["decision"] else "false"].append(case.case_id)
            condition = branches[sid].condition
            if (
                isinstance(condition, Compare)
                and condition.source == "input"
                and isinstance(condition.expected, LiteralRef)
            ):
                value = row["inputs"].get(condition.path)
                if (
                    type(value) is type(condition.expected.value)
                    and value == condition.expected.value
                ):
                    result[sid]["boundary"].append(case.case_id)
    for row in result.values():
        for key in row:
            row[key] = sorted(set(row[key]))
    return result
