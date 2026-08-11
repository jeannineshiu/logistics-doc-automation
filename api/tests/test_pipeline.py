"""Pipeline-level tests: layer interaction and the pure-LLM control group."""

import json
from types import SimpleNamespace

from engine.pipeline import process_document
from models.schemas import Decision, DocType, ExtractionMethod

from tests.conftest import make_pdf

INVOICE_LINES = [
    "INVOICE",
    "Invoice No: INV-2025-00042",
    "Invoice date: 15.03.2025",
    "USt-ID: DE123456789",
    "Total amount: EUR 1.234,56",
    "IBAN: DE89 3704 0044 0532 0130 00",
]


class FakeClient:
    """Returns a fixed value + confidence for every requested field."""

    def __init__(self, value="LLM-VALUE", confidence=0.95, tokens=1000):
        self.calls = 0
        self.requested_fields = []
        self._value, self._conf, self._tokens = value, confidence, tokens
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        prompt = kwargs["messages"][0]["content"][0]["text"]
        fields = [ln.split('"')[1] for ln in prompt.splitlines() if ln.startswith('- "')]
        self.requested_fields.append(fields)
        payload = {"fields": {f: {"value": self._value, "confidence": self._conf} for f in fields}}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
            usage=SimpleNamespace(total_tokens=self._tokens),
        )


def test_rule_layer_resolves_fields_without_calling_llm():
    client = FakeClient()
    res = process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-1", llm_client=client)
    assert res.doc_type == DocType.INVOICE
    assert res.fields.iban.method == ExtractionMethod.RULE
    assert res.fields.iban.confidence == 1.0
    # only supplier_name is unlabeled in this layout, so exactly one LLM call
    assert client.calls == 1
    assert client.requested_fields[0] == ["supplier_name"]
    assert res.tokens_used == 1000


def test_llm_asked_only_for_missing_fields():
    client = FakeClient()
    process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-2", llm_client=client)
    asked = client.requested_fields[0]
    assert "iban" not in asked          # rule layer already validated it
    assert "invoice_number" not in asked


def test_pure_llm_mode_sends_every_field():
    """The control group: rules off means all seven fields go to the model."""
    client = FakeClient()
    res = process_document(
        make_pdf(INVOICE_LINES), "inv.pdf", "doc-3", llm_client=client, rules_enabled=False
    )
    assert sorted(client.requested_fields[0]) == sorted(
        ["invoice_number", "invoice_date", "supplier_name", "supplier_vat_id",
         "currency", "total_amount", "iban"]
    )
    assert client.calls == 1
    # nothing came from the rule layer, so the zero-cost coverage metric is 0
    methods = {f.method for f in res.fields.__dict__.values()}
    assert methods == {ExtractionMethod.LLM}


def test_llm_disabled_leaves_gaps_and_routes_to_review():
    res = process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-4", llm_enabled=False)
    assert res.tokens_used == 0
    assert res.cost_usd == 0
    assert res.decision == Decision.HUMAN_REVIEW
    assert "supplier_name" in res.flagged_fields


def test_budget_exceeded_routes_to_review():
    client = FakeClient(tokens=99_999)   # one call blows the per-document cap
    res = process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-5", llm_client=client)
    assert res.decision == Decision.HUMAN_REVIEW
    assert "<budget_exceeded>" in res.flagged_fields


def test_low_llm_confidence_routes_to_review():
    client = FakeClient(confidence=0.4)
    res = process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-6", llm_client=client)
    assert res.decision == Decision.HUMAN_REVIEW


def test_unknown_document_is_rejected():
    res = process_document(make_pdf(["a memo about nothing"]), "x.pdf", "doc-7", llm_enabled=False)
    assert res.doc_type == DocType.UNKNOWN
    assert res.decision == Decision.REJECT
    assert res.fields is None
