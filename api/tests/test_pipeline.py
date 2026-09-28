"""Pipeline-level tests: layer interaction and the pure-LLM control group."""

import io
import json
from types import SimpleNamespace

from engine import rules
from engine.pipeline import process_document
from models.schemas import Decision, DocType, Evidence, ExtractionMethod
from PIL import Image

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


def test_a_checksum_valid_iban_is_verified():
    res = process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-ev1", llm_client=FakeClient())
    assert res.fields.iban.evidence == Evidence.VERIFIED


NO_IBAN_LINES = [ln for ln in INVOICE_LINES if not ln.startswith("IBAN")]


def test_an_iban_the_model_read_is_verified_when_its_checksum_passes():
    # the checksum is evidence whoever read the value, so a model read can be verified too
    client = FakeClient(value="DE89 3704 0044 0532 0130 00")
    res = process_document(make_pdf(NO_IBAN_LINES), "inv.pdf", "doc-ev4", llm_client=client)
    assert res.fields.iban.method == ExtractionMethod.LLM
    assert res.fields.iban.evidence == Evidence.VERIFIED


def test_an_iban_the_model_read_is_uncorroborated_when_its_checksum_fails():
    client = FakeClient(value="DE00 3704 0044 0532 0130 00")
    res = process_document(make_pdf(NO_IBAN_LINES), "inv.pdf", "doc-ev5", llm_client=client)
    assert res.fields.iban.value is not None
    assert res.fields.iban.evidence == Evidence.UNCORROBORATED


def test_an_unchecked_value_is_uncorroborated_whether_rule_or_model_read_it():
    res = process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-ev2", llm_client=FakeClient())
    # read by the rule layer from its label, by its format, and by the model: one reader's word each
    assert res.fields.invoice_number.method == ExtractionMethod.RULE
    assert res.fields.invoice_number.evidence == Evidence.UNCORROBORATED
    assert res.fields.invoice_date.method == ExtractionMethod.RULE
    assert res.fields.invoice_date.evidence == Evidence.UNCORROBORATED
    assert res.fields.supplier_name.method == ExtractionMethod.LLM
    assert res.fields.supplier_name.evidence == Evidence.UNCORROBORATED


def test_a_missing_field_has_no_evidence():
    res = process_document(
        make_pdf(INVOICE_LINES), "inv.pdf", "doc-ev3", llm_client=FakeClient(value=None, confidence=0)
    )
    assert res.fields.supplier_name.value is None
    assert res.fields.supplier_name.evidence is None


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


def test_complete_text_layer_invoice_auto_approves(invoice_pdf):
    """Pin the CONF_LABELED boundary as a decision, not as a number.

    supplier_name has no validator to pass, so a labeled capture scores
    CONF_LABELED — deliberately equal to the default auto-approve threshold.
    Every other field validates higher, so a fully labeled invoice clears
    review with no LLM call. If either constant drifts, this names the
    decision that changed.
    """
    res = process_document(invoice_pdf, "inv.pdf", "doc-8", llm_enabled=False)
    assert res.decision == Decision.AUTO_APPROVE
    assert res.flagged_fields == []
    assert res.tokens_used == 0
    assert res.fields.supplier_name.method == ExtractionMethod.RULE
    assert res.fields.supplier_name.confidence == rules.CONF_LABELED


US_INVOICE_LINES = [
    "INVOICE",
    "Invoice No: 22334",
    "Invoice date: 03/25/2025",
    "Supplier: Cumulus Broadcasting LLC",
    "Total amount: USD 1,082.50",
]


class SparseClient(FakeClient):
    """Answers only for the named fields — a document that does not fit the schema."""

    def __init__(self, answer_fields, **kwargs):
        self._answer = set(answer_fields)
        super().__init__(**kwargs)

    def _create(self, **kwargs):
        self.calls += 1
        prompt = kwargs["messages"][0]["content"][0]["text"]
        fields = [ln.split('"')[1] for ln in prompt.splitlines() if ln.startswith('- "')]
        self.requested_fields.append(fields)
        payload = {
            "fields": {
                name: (
                    {"value": self._value, "confidence": self._conf}
                    if name in self._answer
                    else {"value": None, "confidence": 0}
                )
                for name in fields
            }
        }
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
            usage=SimpleNamespace(total_tokens=self._tokens),
        )


def _blank_jpeg() -> bytes:
    """An image upload: no text layer, so the whole document goes to the model."""
    buf = io.BytesIO()
    Image.new("RGB", (200, 300), "white").save(buf, format="JPEG")
    return buf.getvalue()


def test_a_partly_read_image_keeps_its_fields_instead_of_being_rejected():
    """Two of seven fields does not identify a document type, but it is not
    nothing: the total the model read is worth a review queue, not a bin."""
    client = SparseClient({"invoice_number", "total_amount"})
    res = process_document(_blank_jpeg(), "receipt.jpg", "doc-9", llm_client=client)
    assert res.doc_type == DocType.INVOICE
    assert res.decision == Decision.HUMAN_REVIEW
    assert res.fields.total_amount.value == "LLM-VALUE"
    assert "<doc_type_uncertain>" in res.flagged_fields


def test_an_image_nothing_was_read_from_is_still_rejected():
    client = SparseClient(set())
    res = process_document(_blank_jpeg(), "blank.jpg", "doc-10", llm_client=client)
    assert res.doc_type == DocType.UNKNOWN
    assert res.decision == Decision.REJECT
    assert res.fields is None


def test_us_invoice_is_unapprovable_until_the_required_set_says_so(monkeypatch):
    """The same document, the same extraction, two configurations.

    Measured on DocILE: an IBAN is absent from every US invoice, so with the
    default required set no US document can ever clear review.
    """
    res = process_document(make_pdf(US_INVOICE_LINES), "us.pdf", "doc-11", llm_enabled=False)
    assert res.decision == Decision.HUMAN_REVIEW
    # A US layout matches one classifier keyword and lands at 0.6 — one hair
    # above the reject floor — so the type is flagged as a guess alongside the
    # two fields the document does not carry.
    assert set(res.flagged_fields) == {"<doc_type_uncertain>", "iban", "supplier_vat_id"}

    monkeypatch.setenv(
        "REQUIRED_FIELDS_INVOICE",
        "invoice_number,invoice_date,supplier_name,currency,total_amount",
    )
    res = process_document(make_pdf(US_INVOICE_LINES), "us.pdf", "doc-12", llm_enabled=False)
    assert res.decision == Decision.AUTO_APPROVE
    assert res.flagged_fields == []
    assert res.fields.iban.value is None, "still absent, just no longer disqualifying"
