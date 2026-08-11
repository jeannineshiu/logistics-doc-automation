"""API tests — LLM disabled (LLM_ENABLED=0), so extraction is rule-layer only.
No OpenAI credits are spent; everything runs in CI."""


def _upload(client, pdf_bytes, name="doc.pdf"):
    return client.post("/extract", files={"file": (name, pdf_bytes, "application/pdf")})


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


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


def test_metrics_endpoint(client, invoice_pdf):
    _upload(client, invoice_pdf)
    text = client.get("/metrics").text
    assert "documents_processed_total 1" in text
    assert "human_review_rate" in text
    assert "tokens_used_total" in text
