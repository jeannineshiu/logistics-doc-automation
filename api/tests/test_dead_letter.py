"""Dead-letter queue: the record that a document failed for good.

Without these rows a document that exhausted every n8n retry left no trace on
the API side — the error workflow only notified.
"""


def _failure(execution_id="42", **over):
    body = {
        "workflow_name": "Document Processing Pipeline",
        "execution_id": execution_id,
        "node_name": "Call Extract API",
        "error_message": "connect ECONNREFUSED api:8000",
        "payload": {"filename": "invoice_001.pdf"},
    }
    body.update(over)
    return body


def test_records_a_failure(client):
    resp = client.post("/dead-letter", json=_failure())
    assert resp.status_code == 200
    assert resp.json()["status"] == "open"
    assert resp.json()["deduplicated"] is False

    items = client.get("/dead-letter").json()["items"]
    assert len(items) == 1
    assert items[0]["execution_id"] == "42"
    assert items[0]["payload"] == {"filename": "invoice_001.pdf"}


def test_redelivery_of_the_same_execution_does_not_duplicate(client):
    client.post("/dead-letter", json=_failure())
    resp = client.post("/dead-letter", json=_failure())
    assert resp.json()["deduplicated"] is True
    assert client.get("/dead-letter").json()["total"] == 1


def test_distinct_executions_are_separate_entries(client):
    client.post("/dead-letter", json=_failure("1"))
    client.post("/dead-letter", json=_failure("2"))
    assert client.get("/dead-letter").json()["total"] == 2


def test_resolve_clears_it_from_the_open_queue(client):
    entry_id = client.post("/dead-letter", json=_failure()).json()["id"]
    assert client.post(f"/dead-letter/{entry_id}/resolve").json()["status"] == "resolved"

    assert client.get("/dead-letter").json()["total"] == 0
    assert client.get("/dead-letter?status=resolved").json()["total"] == 1


def test_resolve_unknown_entry_404s(client):
    assert client.post("/dead-letter/999/resolve").status_code == 404


def test_backlog_is_exposed_as_an_alertable_metric(client):
    assert "dead_letter_open 0" in client.get("/metrics").text

    client.post("/dead-letter", json=_failure("1"))
    client.post("/dead-letter", json=_failure("2"))
    assert "dead_letter_open 2" in client.get("/metrics").text

    entry_id = client.get("/dead-letter").json()["items"][0]["id"]
    client.post(f"/dead-letter/{entry_id}/resolve")
    assert "dead_letter_open 1" in client.get("/metrics").text
