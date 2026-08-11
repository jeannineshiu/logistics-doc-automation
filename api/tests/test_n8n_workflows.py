"""The committed n8n workflow JSON is source too — check it in CI.

A renamed node leaves a dangling connection that only fails at import time,
and a broken errorWorkflow id disables exception handling silently.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "n8n"))

from validate_workflows import ERROR_ID, MAIN_ID, check, load_workflows


def test_workflows_are_structurally_valid():
    assert check(load_workflows()) == []


def test_both_fixed_ids_are_present():
    ids = {wf["id"] for wf in load_workflows().values()}
    assert {MAIN_ID, ERROR_ID} <= ids


def test_error_workflow_stays_wired():
    main = next(wf for wf in load_workflows().values() if wf["id"] == MAIN_ID)
    assert main["settings"]["errorWorkflow"] == ERROR_ID


def test_error_handler_records_to_the_dead_letter_queue():
    """The Error Handler must persist failures, not only notify."""
    error_wf = next(wf for wf in load_workflows().values() if wf["id"] == ERROR_ID)
    urls = [
        node["parameters"].get("url", "")
        for node in error_wf["nodes"]
        if node["type"].endswith("httpRequest")
    ]
    assert any("/dead-letter" in url for url in urls)


def test_extract_call_retries():
    main = next(wf for wf in load_workflows().values() if wf["id"] == MAIN_ID)
    node = next(n for n in main["nodes"] if n["name"] == "Call Extract API")
    assert node["retryOnFail"] is True
    assert node["maxTries"] == 3


def test_reviewer_json_is_validated_before_it_reaches_the_api():
    """Parsing the reviewer's textarea inline in the HTTP node threw a raw
    SyntaxError and lost the correction — it goes through a Code node now."""
    main = next(wf for wf in load_workflows().values() if wf["id"] == MAIN_ID)
    submit = next(n for n in main["nodes"] if n["name"] == "Review: Submit Corrections")
    assert "JSON.parse" not in submit["parameters"]["jsonBody"]

    validator = next(n for n in main["nodes"] if n["name"] == "Review: Validate Corrections")
    assert validator["type"].endswith("code")
    assert "JSON.parse" in validator["parameters"]["jsCode"]
    # $json is only bound per item; in the default all-items mode the node blew
    # up with "undefined" and lost the reviewer's input it exists to preserve.
    assert validator["parameters"]["mode"] == "runOnceForEachItem"

    wait_targets = main["connections"]["Review: Wait for Human (Form)"]["main"][0]
    assert wait_targets[0]["node"] == "Review: Validate Corrections"


def test_invalid_corrections_are_routed_not_thrown():
    """A thrown Error reaches the error workflow but n8n drops the message
    across the task-runner boundary ("undefined [line N]"), losing the raw
    input. Invalid input is routed as data instead."""
    main = next(wf for wf in load_workflows().values() if wf["id"] == MAIN_ID)
    validator = next(n for n in main["nodes"] if n["name"] == "Review: Validate Corrections")
    assert "throw new Error" not in validator["parameters"]["jsCode"]

    branches = main["connections"]["Review: Corrections Valid?"]["main"]
    assert branches[0][0]["node"] == "Review: Submit Corrections"
    assert branches[1][0]["node"] == "Review: Record Invalid Correction"

    recorder = next(n for n in main["nodes"] if n["name"] == "Review: Record Invalid Correction")
    body = recorder["parameters"]["jsonBody"]
    assert "/dead-letter" in recorder["parameters"]["url"]
    assert "submitted" in body and "$json.raw" in body, "reviewer's raw input must be preserved"
