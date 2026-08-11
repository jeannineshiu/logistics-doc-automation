"""Structural checks on the exported n8n workflows.

The workflow JSON is committed source, but nothing type-checks it: a renamed
node leaves a dangling connection that only surfaces at import time, and a
broken `errorWorkflow` id silently disables exception handling — n8n just logs
"is not active and cannot be executed" and carries on.

Run standalone (`python n8n/validate_workflows.py`) or via pytest in CI.
"""

import json
import sys
from pathlib import Path

WORKFLOW_DIR = Path(__file__).resolve().parent / "workflows"
MAIN_ID = "LogisticsDocProc"
ERROR_ID = "LogisticsErrorWf"


def load_workflows() -> dict[str, dict]:
    return {p.name: json.loads(p.read_text()) for p in sorted(WORKFLOW_DIR.glob("*.json"))}


def check(workflows: dict[str, dict]) -> list[str]:
    problems: list[str] = []
    ids = {wf.get("id") for wf in workflows.values()}

    for filename, wf in workflows.items():
        for key in ("id", "name", "nodes", "connections"):
            if key not in wf:
                problems.append(f"{filename}: missing top-level key '{key}'")
        nodes = wf.get("nodes", [])
        names = [n.get("name") for n in nodes]

        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            problems.append(f"{filename}: duplicate node names {sorted(duplicates)}")

        known = set(names)
        for source, outputs in wf.get("connections", {}).items():
            if source not in known:
                problems.append(f"{filename}: connection from unknown node '{source}'")
            for branch in outputs.get("main", []):
                for link in branch or []:
                    target = link.get("node")
                    if target not in known:
                        problems.append(
                            f"{filename}: '{source}' connects to unknown node '{target}'"
                        )

        # A node with no inbound connection and no trigger role is unreachable.
        targets = {
            link.get("node")
            for outputs in wf.get("connections", {}).values()
            for branch in outputs.get("main", [])
            for link in branch or []
        }
        for node in nodes:
            node_type = node.get("type", "")
            if node_type.endswith("stickyNote"):
                continue
            is_trigger = "trigger" in node_type.lower() or node_type.endswith("webhook")
            if not is_trigger and node.get("name") not in targets:
                problems.append(f"{filename}: node '{node['name']}' is unreachable")

    if MAIN_ID not in ids:
        problems.append(f"no workflow carries the fixed main id '{MAIN_ID}'")
    if ERROR_ID not in ids:
        problems.append(f"no workflow carries the fixed error id '{ERROR_ID}'")

    for filename, wf in workflows.items():
        if wf.get("id") == MAIN_ID:
            wired = wf.get("settings", {}).get("errorWorkflow")
            if wired != ERROR_ID:
                problems.append(
                    f"{filename}: settings.errorWorkflow is {wired!r}, expected {ERROR_ID!r} "
                    "— exception handling would be silently disabled"
                )
    return problems


def main() -> int:
    problems = check(load_workflows())
    for problem in problems:
        print(f"ERROR {problem}")
    print("n8n workflows OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
