"""API tests — LLM disabled (LLM_ENABLED=0), so extraction is rule-layer only.
No OpenAI credits are spent; everything runs in CI."""


def _upload(client, pdf_bytes, name="doc.pdf"):
    return client.post("/extract", files={"file": (name, pdf_bytes, "application/pdf")})


def test_live(client):
    assert client.get("/live").json() == {"status": "ok"}


def test_health_ok_when_database_reachable(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok"}


def test_health_503_when_database_unreachable(client):
    """The regression this endpoint exists for: /health must not report ok
    while the database is down — see the docstring in main.health."""
    from main import app
    from models.db import get_session
    from sqlalchemy.exc import OperationalError

    def broken_session():
        class Broken:
            def execute(self, *_a, **_kw):
                raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        yield Broken()

    app.dependency_overrides[get_session] = broken_session
    try:
        resp = client.get("/health")
        assert resp.status_code == 503
        assert resp.json()["status"] == "unavailable"
    finally:
        app.dependency_overrides.clear()


def test_extract_invoice_rule_layer(client, invoice_pdf):
    resp = _upload(client, invoice_pdf)
    assert resp.status_code == 200
    body = resp.json()
    assert body["doc_type"] == "invoice"
    assert body["fields"]["invoice_number"]["value"] == "INV-2025-00042"
    assert body["fields"]["iban"]["value"] == "DE89370400440532013000"
    assert body["fields"]["iban"]["confidence"] == 1.0
    assert body["fields"]["total_amount"]["value"] == "1234.56"
    assert body["tokens_used"] == 0  # deterministic layer only
    assert body["cost_usd"] == 0


def test_extract_is_idempotent(client, invoice_pdf):
    first = _upload(client, invoice_pdf).json()
    second = _upload(client, invoice_pdf).json()
    assert first["document_id"] == second["document_id"]
    # still only one document stored
    docs = client.get("/documents").json()
    assert docs["total"] == 1


def test_unsupported_file_type(client):
    resp = client.post("/extract", files={"file": ("x.txt", b"hello", "text/plain")})
    assert resp.status_code == 415


def test_empty_file_rejected(client):
    resp = client.post("/extract", files={"file": ("x.pdf", b"", "application/pdf")})
    assert resp.status_code == 400


def test_document_detail_and_audit_trail(client, invoice_pdf):
    doc_id = _upload(client, invoice_pdf).json()["document_id"]
    detail = client.get(f"/documents/{doc_id}").json()
    assert detail["document_id"] == doc_id
    assert len(detail["audit_trail"]) == 1
    assert detail["audit_trail"][0]["actor"] == "system"


def test_document_not_found(client):
    assert client.get("/documents/nope").status_code == 404


def test_review_flow(client, invoice_pdf):
    body = _upload(client, invoice_pdf).json()
    doc_id = body["document_id"]
    if body["decision"] != "human_review":
        return  # nothing to review in this corpus variant
    resp = client.post(
        f"/review/{doc_id}",
        json={"corrected_fields": {"supplier_name": "Corrected Logistik GmbH"}, "reviewer": "jeannine"},
    )
    assert resp.status_code == 200
    assert resp.json()["corrections"]["supplier_name"]["to"] == "Corrected Logistik GmbH"
    detail = client.get(f"/documents/{doc_id}").json()
    assert detail["status"] == "approved"
    assert detail["fields"]["supplier_name"]["confidence"] == 1.0
    assert any(a["action"] == "review_submitted" for a in detail["audit_trail"])


def test_review_wrong_status_conflict(client, invoice_pdf):
    body = _upload(client, invoice_pdf).json()
    if body["decision"] == "human_review":
        client.post(
            f"/review/{body['document_id']}",
            json={"corrected_fields": {}, "reviewer": "j"},
        )
    resp = client.post(
        f"/review/{body['document_id']}",
        json={"corrected_fields": {}, "reviewer": "j"},
    )
    assert resp.status_code == 409


def test_replay_reports_current_status_not_the_frozen_decision(client):
    """Re-sending an already-approved document used to route back into review.

    /extract is idempotent by file hash and replayed the stored `decision`,
    which stays `human_review` forever. Orchestration branched on that, sent a
    resolved document to the HITL form again, and the write-back 409'd —
    producing a spurious review task and a spurious dead-letter entry.
    """
    from tests.conftest import make_pdf

    # No IBAN and no VAT id, so the rule layer leaves gaps and it needs review.
    pdf = make_pdf(["INVOICE", "Invoice No: INV-2025-00099", "Supplier: Gap GmbH"])

    first = _upload(client, pdf, "replay.pdf").json()
    assert first["decision"] == "human_review"
    assert first["status"] == "pending_review"

    resp = client.post(
        f"/review/{first['document_id']}",
        json={"corrected_fields": {}, "reviewer": "jeannine"},
    )
    assert resp.status_code == 200

    replay = _upload(client, pdf, "replay.pdf").json()
    assert replay["document_id"] == first["document_id"]
    assert replay["decision"] == "human_review", "the engine's decision is a historical record"
    assert replay["status"] == "approved", "but the current disposition must be reported"


def test_fresh_extraction_derives_status_from_decision(client, invoice_pdf):
    body = _upload(client, invoice_pdf).json()
    expected = {
        "auto_approve": "approved",
        "human_review": "pending_review",
        "reject": "rejected",
    }[body["decision"]]
    assert body["status"] == expected


def test_metrics_endpoint(client, invoice_pdf):
    _upload(client, invoice_pdf)
    text = client.get("/metrics").text
    assert "documents_processed_total 1" in text
    assert "human_review_rate" in text
    assert "tokens_used_total" in text
