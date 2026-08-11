"""Pin the mechanically checkable claims in the README.

The workflow JSON has validate_workflows.py and the code has this suite, but
the README had nothing — so it drifted. Renaming a node, adding an HTTP call or
an endpoint left prose that was quietly wrong: a stale node count, a removed
endpoint, a test count that no longer matched.

These tests only check what can be verified without judgement — names, counts,
paths, thresholds. Nothing here validates that the prose is *true*, only that
the things it names still exist and the numbers it quotes still hold.
"""

import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text()
WORKFLOWS = {
    p.name: json.loads(p.read_text())
    for p in sorted((ROOT / "n8n" / "workflows").glob("*.json"))
}
NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def test_every_linked_image_exists():
    missing = [
        path
        for _, path in re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", README)
        if not (ROOT / path).exists()
    ]
    assert not missing, f"README links images that are not in the repo: {missing}"


def test_no_orphaned_images_in_docs():
    """An image nobody references is usually a rename that half-landed."""
    linked = set(re.findall(r"!\[[^\]]*\]\(([^)]+)\)", README))
    on_disk = {
        f"docs/{p.name}"
        for p in (ROOT / "docs").iterdir()
        if p.suffix.lower() in {".png", ".svg", ".jpg", ".gif"}
    }
    assert not on_disk - linked, f"unreferenced files in docs/: {sorted(on_disk - linked)}"


def test_markdown_tables_are_not_split():
    """A paragraph inserted between rows orphans the rest of the table, which
    renders as broken text on GitHub. This actually happened."""
    lines = README.split("\n")
    blocks, cur = [], []
    for i, line in enumerate(lines, 1):
        if line.strip().startswith("|"):
            cur.append((i, line))
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)

    for block in blocks:
        counts = {row.count("|") for _, row in block}
        assert len(counts) == 1, (
            f"table starting at line {block[0][0]} has rows with differing column "
            f"counts {sorted(counts)} — it is probably split or malformed"
        )
        assert "---" in block[1][1], (
            f"table starting at line {block[0][0]} has no header separator on its "
            "second row — a preceding table was probably broken apart"
        )


def test_documented_endpoints_exist():
    app_paths = _app_paths()
    documented = {
        "/" + m for m in re.findall(r"http://localhost:8000/([a-z-]+)", README)
    }
    unknown = {p for p in documented if p not in app_paths and p != "/docs"}
    assert not unknown, f"README advertises endpoints the API does not serve: {sorted(unknown)}"


def _app_paths() -> set[str]:
    """Read the served surface from the OpenAPI schema.

    app.routes is not usable here: FastAPI wraps included routers in
    _IncludedRouter objects that expose neither `path` nor `routes`, so walking
    it silently misses every endpoint that lives in a router.
    """
    from main import app

    return set(app.openapi()["paths"])


def test_named_n8n_nodes_exist():
    """Every node the README names by backtick must exist in a workflow."""
    all_nodes = {n["name"] for wf in WORKFLOWS.values() for n in wf["nodes"]}
    named = {
        name
        for name in re.findall(r"`([A-Z][^`]{3,45})`", README)
        if name.startswith(("Call ", "Review: ", "Record ", "Switch ", "Webhook"))
    }
    missing = named - all_nodes
    assert not missing, f"README names nodes that no longer exist: {sorted(missing)}"


def test_http_node_count_claim_matches_the_workflows():
    """The n8n Cloud note tells the reader how many base URLs to change. Adding
    an HTTP node without updating it sends them to a half-migrated setup."""
    match = re.search(r"in all (\w+) HTTP nodes", README)
    assert match, "the n8n Cloud note no longer states how many HTTP nodes to change"
    claimed = NUMBER_WORDS[match.group(1)]

    actual = [
        n["name"]
        for wf in WORKFLOWS.values()
        for n in wf["nodes"]
        if n["type"].endswith("httpRequest") and "api:8000" in n["parameters"].get("url", "")
    ]
    assert claimed == len(actual), (
        f"README says {claimed} HTTP nodes point at the API, but the workflows have "
        f"{len(actual)}: {sorted(actual)}"
    )


def test_repo_layout_paths_exist():
    block = re.search(r"## Repo layout\s*```\n(.*?)```", README, re.DOTALL)
    assert block, "the repo layout block is gone"
    missing = []
    for line in block.group(1).strip().split("\n"):
        path = line.split()[0]
        if not (ROOT / path).exists():
            missing.append(path)
    assert not missing, f"repo layout lists paths that do not exist: {missing}"


def test_quoted_test_count_matches_reality():
    """Ask pytest to count, rather than grepping for `def test_` — that proxy
    undercounts parametrised cases and would report a wrong target number."""
    match = re.search(r"(\d+) pytest tests", README)
    assert match, "the README no longer states a test count"
    claimed = int(match.group(1))

    # --collect-only does not execute anything, so this cannot recurse.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "api/tests"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    collected = re.search(r"(\d+) tests? collected", result.stdout)
    assert collected, f"could not read a collection count from pytest:\n{result.stdout[-500:]}"
    actual = int(collected.group(1))

    assert claimed == actual, (
        f"README claims {claimed} tests, pytest collects {actual}. Update the README."
    )


@pytest.mark.parametrize(
    "quoted, source",
    [
        ("MAX_TOKENS_PER_DOC=8000", ".env.example"),
        ("MAX_LLM_CALLS_PER_DOC=2", ".env.example"),
    ],
)
def test_quoted_env_defaults_match_the_example(quoted, source):
    assert quoted in README, f"README no longer documents {quoted}"
    assert quoted in (ROOT / source).read_text(), (
        f"README quotes {quoted} but {source} disagrees"
    )


def test_every_env_var_named_is_real():
    """Catches a renamed or deleted setting still being documented."""
    known = (ROOT / ".env.example").read_text() + (ROOT / "docker-compose.yml").read_text()
    named = set(re.findall(r"`([A-Z][A-Z0-9_]{4,})`", README))
    unknown = {v for v in named if v not in known}
    assert not unknown, f"README documents settings that are not configured anywhere: {sorted(unknown)}"


def test_routing_thresholds_match_the_code():
    from engine import confidence

    assert "< 0.6" in README, "the reject floor is no longer documented"
    assert confidence.DOC_TYPE_FLOOR == 0.6, (
        f"README documents a 0.6 reject floor, code uses {confidence.DOC_TYPE_FLOOR}"
    )
    assert "≥ 0.90" in README, "the auto-approve threshold is no longer documented"
    assert "AUTO_APPROVE_THRESHOLD=0.90" in (ROOT / ".env.example").read_text(), (
        "README documents a 0.90 auto-approve threshold, .env.example disagrees"
    )
