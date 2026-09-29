"""Pipeline-level tests: layer interaction and the pure-LLM control group."""

import io
import json
from types import SimpleNamespace

import httpx
import pytest
from engine.pipeline import process_document
from models.schemas import Decision, DocType, Evidence, ExtractionMethod, FlagReason
from openai import APIConnectionError
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


def replace_line(prefix: str, line: str) -> list[str]:
    return [ln for ln in INVOICE_LINES if not ln.startswith(prefix)] + [line]


class FakeClient:
    """Returns a fixed value + confidence for every requested field, or a
    per-field value from `values` where one is given."""

    def __init__(
        self, value="LLM-VALUE", confidence=0.95, tokens=1000, values=None, error=None, content=None
    ):
        self.calls = 0
        self.requested_fields = []
        self.prompts = []
        self._value, self._conf, self._tokens = value, confidence, tokens
        self._values = values or {}
        self._error, self._content = error, content
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        if self._error:
            raise self._error
        prompt = kwargs["messages"][0]["content"][0]["text"]
        self.prompts.append(prompt)
        fields = [ln.split('"')[1] for ln in prompt.splitlines() if ln.startswith('- "')]
        self.requested_fields.append(fields)
        payload = {"fields": {
            f: {"value": self._values.get(f, self._value), "confidence": self._conf} for f in fields
        }}
        content = self._content if self._content is not None else json.dumps(payload)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(total_tokens=self._tokens),
        )


def test_the_gaps_and_the_corroboration_go_out_in_one_llm_call():
    client = FakeClient()
    res = process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-1", llm_client=client)
    assert res.doc_type == DocType.INVOICE
    assert res.fields.iban.method == ExtractionMethod.RULE
    assert res.fields.iban.confidence == 1.0
    # supplier_name is unlabeled in this layout and the rest need a second reader:
    # still exactly one LLM call
    assert client.calls == 1
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


def test_a_rule_value_the_model_reproduces_is_corroborated_in_one_call():
    client = FakeClient(values={"invoice_number": "INV-2025-00042"})
    res = process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-co1", llm_client=client)
    assert res.fields.invoice_number.value == "INV-2025-00042"
    assert res.fields.invoice_number.evidence == Evidence.CORROBORATED
    assert client.calls == 1


def test_a_rule_value_the_model_contradicts_is_a_conflict_carrying_both_candidates():
    # a DocILE failure shape: the rule layer took a line amount for the total
    lines = replace_line("Total", "Total amount: EUR 121,72")
    client = FakeClient(values={"total_amount": "243.44"})
    res = process_document(make_pdf(lines), "inv.pdf", "doc-co2", llm_client=client)
    total = res.fields.total_amount
    assert total.evidence == Evidence.UNCORROBORATED
    assert [(c.value, c.method) for c in total.candidates] == [
        ("121.72", ExtractionMethod.RULE),
        ("243.44", ExtractionMethod.LLM),
    ]


def test_an_amount_or_date_formatted_differently_by_the_two_readers_still_corroborates():
    client = FakeClient(values={"total_amount": "1,234.56", "invoice_date": "15 March 2025"})
    res = process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-co4", llm_client=client)
    assert res.fields.total_amount.evidence == Evidence.CORROBORATED
    assert res.fields.invoice_date.evidence == Evidence.CORROBORATED


@pytest.mark.parametrize(
    ("field", "text_line", "rule_value", "model_value"),
    [
        # the three wrong values the rule layer read off real DocILE invoices
        ("total_amount", "Total amount: EUR 903,74", "903.74", "10903.74"),        # truncated
        ("total_amount", "Total amount: EUR 121,72", "121.72", "243.44"),          # a line amount
        ("invoice_number", "Invoice No: PMILD3-98", "PMILD3-98", "PM/LD3-98"),    # misread character
    ],
    ids=["truncated-total", "line-amount-as-total", "misread-character"],
)
def test_the_docile_failure_shapes_end_in_conflict_when_the_model_reads_right(
    field, text_line, rule_value, model_value
):
    lines = replace_line(text_line.split(":")[0], text_line)
    client = FakeClient(values={field: model_value})
    res = process_document(make_pdf(lines), "inv.pdf", f"doc-{field}", llm_client=client)
    result = getattr(res.fields, field)
    assert result.value == rule_value
    assert result.evidence == Evidence.UNCORROBORATED
    assert [c.value for c in result.candidates] == [rule_value, model_value]


def test_the_prompt_never_reveals_what_the_rule_layer_read():
    # a model shown the rule layer's answer is no longer an independent reader
    # values chosen so none can coincide with the prompt's own format examples
    lines = [
        "INVOICE",
        "Invoice No: INV-7731-QZ",
        "Invoice date: 21.08.2024",
        "USt-ID: DE987654321",
        "Total amount: CHF 8.642,19",
    ]
    client = FakeClient()
    res = process_document(make_pdf(lines), "inv.pdf", "doc-co5", llm_client=client)
    read = [f.value for _, f in res.fields if f.method == ExtractionMethod.RULE]
    assert {"INV-7731-QZ", "2024-08-21", "DE987654321", "8642.19"} <= set(read)
    for value in (*read, "21.08.2024", "8.642,19"):
        assert value not in client.prompts[0]


def test_a_rule_value_the_model_could_not_read_stays_uncorroborated_without_conflict():
    client = FakeClient(values={"invoice_number": None})
    res = process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-co6", llm_client=client)
    assert res.fields.invoice_number.value == "INV-2025-00042"
    assert res.fields.invoice_number.evidence == Evidence.UNCORROBORATED
    assert res.fields.invoice_number.candidates == []


def test_with_the_model_disabled_nothing_is_corroborated():
    res = process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-co7", llm_enabled=False)
    assert res.fields.iban.evidence == Evidence.VERIFIED
    others = [f for name, f in res.fields if name != "iban" and f.value is not None]
    assert others and all(f.evidence == Evidence.UNCORROBORATED for f in others)


@pytest.mark.parametrize(
    ("model_value", "evidence"),
    [
        ("2025-05-04", Evidence.CORROBORATED),     # the same day, written as ISO
        ("04 May 2025", Evidence.CORROBORATED),    # the same day, spelled out
        ("05.04.2025", Evidence.UNCORROBORATED),   # 5 April: a different day, not a format
    ],
)
def test_an_ambiguous_date_corroborates_only_when_it_is_the_same_day(model_value, evidence):
    lines = replace_line("Invoice date", "Invoice date: 04.05.2025")   # 4 May, day first
    client = FakeClient(values={"invoice_date": model_value})
    res = process_document(make_pdf(lines), "inv.pdf", "doc-dt", llm_client=client)
    assert res.fields.invoice_date.value == "2025-05-04"
    assert res.fields.invoice_date.evidence == evidence


def test_an_iso_date_printed_on_the_document_is_read_as_written():
    lines = replace_line("Invoice date", "Invoice date: 2025-05-04")
    res = process_document(make_pdf(lines), "inv.pdf", "doc-iso", llm_enabled=False)
    assert res.fields.invoice_date.value == "2025-05-04"


def test_an_amount_that_differs_past_the_second_decimal_is_a_conflict():
    lines = replace_line("Total", "Total amount: EUR 1,23")
    client = FakeClient(values={"total_amount": "1.234"})
    res = process_document(make_pdf(lines), "inv.pdf", "doc-amt", llm_client=client)
    assert res.fields.total_amount.evidence == Evidence.UNCORROBORATED
    assert [c.value for c in res.fields.total_amount.candidates] == ["1.23", "1.234"]


COMPLETE_LINES = [*INVOICE_LINES, "Supplier: Muster Logistik GmbH"]

# What a model reading COMPLETE_LINES correctly returns, in its own formats.
AGREEING = {
    "invoice_number": "INV-2025-00042",
    "invoice_date": "15 March 2025",
    "supplier_name": "Muster Logistik GmbH",
    "supplier_vat_id": "DE123456789",
    "currency": "EUR",
    "total_amount": "1,234.56",
}


@pytest.mark.parametrize(
    "client",
    [
        FakeClient(error=APIConnectionError(request=httpx.Request("POST", "https://x"))),
        FakeClient(content="not json"),                                 # unparseable, twice
    ],
    ids=["api-error", "unparseable"],
)
def test_a_failed_corroboration_call_leaves_the_document_as_the_rule_layer_read_it(client):
    # every field is in the text layer, so the call was only ever for corroboration
    pdf = make_pdf(COMPLETE_LINES)
    without_model = process_document(pdf, "inv.pdf", "doc-f0", llm_enabled=False)
    res = process_document(pdf, "inv.pdf", "doc-f1", llm_client=client)
    assert client.calls >= 1
    assert res.decision == without_model.decision == Decision.HUMAN_REVIEW
    assert res.flagged_fields == without_model.flagged_fields
    assert res.fields == without_model.fields


def test_a_corroboration_call_that_exceeds_the_budget_is_flagged_as_such():
    # a budget overrun must never become an approval, nor pass for an ordinary doubt
    client = FakeClient(tokens=9000, values=AGREEING)                  # blows MAX_TOKENS_PER_DOC
    res = process_document(make_pdf(COMPLETE_LINES), "inv.pdf", "doc-f2", llm_client=client)
    assert res.decision == Decision.HUMAN_REVIEW
    assert res.flagged_fields[0] == "<budget_exceeded>"
    assert res.fields.invoice_number.evidence == Evidence.UNCORROBORATED


def test_a_corroborated_value_carries_no_candidates():
    client = FakeClient(values={"invoice_number": "INV-2025-00042"})
    res = process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-co3", llm_client=client)
    assert res.fields.invoice_number.candidates == []


def test_llm_asked_for_missing_and_unproven_fields_but_not_verified_ones():
    client = FakeClient()
    process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-2", llm_client=client)
    asked = client.requested_fields[0]
    assert "supplier_name" in asked     # missing
    assert "invoice_number" in asked    # rule-read, needs a second reader
    assert "iban" not in asked          # checksum already proved it


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


def test_unknown_document_is_rejected():
    res = process_document(make_pdf(["a memo about nothing"]), "x.pdf", "doc-7", llm_enabled=False)
    assert res.doc_type == DocType.UNKNOWN
    assert res.decision == Decision.REJECT
    assert res.fields is None


def test_a_document_whose_required_fields_are_all_verified_or_corroborated_auto_approves():
    client = FakeClient(values=AGREEING)
    res = process_document(make_pdf(COMPLETE_LINES), "inv.pdf", "doc-8", llm_client=client)
    assert res.decision == Decision.AUTO_APPROVE
    assert res.flagged_fields == []
    assert res.flag_reasons == {}
    assert res.fields.iban.evidence == Evidence.VERIFIED
    assert all(f.evidence == Evidence.CORROBORATED for name, f in res.fields if name != "iban")
    assert client.calls == 1


def test_field_confidence_plays_no_part_in_routing():
    # the model's own confidence is a claim: agreement at 0.1 is still agreement,
    # and a read nobody corroborates at 1.0 is still one reader's word
    doubtful = FakeClient(values=AGREEING, confidence=0.1)
    res = process_document(make_pdf(COMPLETE_LINES), "inv.pdf", "doc-c1", llm_client=doubtful)
    assert res.decision == Decision.AUTO_APPROVE

    sure = FakeClient(values=AGREEING, confidence=1.0)
    res = process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-c2", llm_client=sure)
    assert res.fields.supplier_name.confidence == 1.0
    assert res.decision == Decision.HUMAN_REVIEW


def test_a_required_field_only_the_model_read_is_flagged_uncorroborated():
    # INVOICE_LINES has no supplier line: the model is its only reader
    client = FakeClient(values=AGREEING)
    res = process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-r1", llm_client=client)
    assert res.fields.supplier_name.method == ExtractionMethod.LLM
    assert res.decision == Decision.HUMAN_REVIEW
    assert res.flagged_fields == ["supplier_name"]
    assert res.flag_reasons == {"supplier_name": FlagReason.UNCORROBORATED}


def test_a_required_field_in_conflict_is_flagged_as_a_conflict():
    client = FakeClient(values=AGREEING | {"total_amount": "10903.74"})
    res = process_document(make_pdf(COMPLETE_LINES), "inv.pdf", "doc-r2", llm_client=client)
    assert res.decision == Decision.HUMAN_REVIEW
    assert res.flagged_fields == ["total_amount"]
    assert res.flag_reasons == {"total_amount": FlagReason.CONFLICT}


def test_a_required_field_nobody_read_is_flagged_missing():
    client = FakeClient(values=AGREEING | {"supplier_name": None})
    res = process_document(make_pdf(INVOICE_LINES), "inv.pdf", "doc-r3", llm_client=client)
    assert res.decision == Decision.HUMAN_REVIEW
    assert res.flag_reasons == {"supplier_name": FlagReason.MISSING}


def test_an_uncorroborated_optional_field_does_not_block_auto_approval(monkeypatch):
    monkeypatch.setenv(
        "REQUIRED_FIELDS_INVOICE", "invoice_number,invoice_date,supplier_name,total_amount"
    )
    # the model reads the VAT id differently and cannot read the currency at all
    client = FakeClient(values=AGREEING | {"supplier_vat_id": "DE123456780", "currency": None})
    res = process_document(make_pdf(COMPLETE_LINES), "inv.pdf", "doc-o1", llm_client=client)
    assert res.decision == Decision.AUTO_APPROVE
    assert res.flagged_fields == []
    # stored with what stands behind it, so a consumer can tell doubt from absence
    assert res.fields.currency.value == "EUR"
    assert res.fields.currency.evidence == Evidence.UNCORROBORATED
    assert len(res.fields.supplier_vat_id.candidates) == 2


def test_with_the_model_disabled_nothing_unverified_auto_approves():
    res = process_document(make_pdf(COMPLETE_LINES), "inv.pdf", "doc-d1", llm_enabled=False)
    assert res.decision == Decision.HUMAN_REVIEW
    assert res.tokens_used == 0
    assert set(res.flagged_fields) == set(AGREEING)
    assert set(res.flag_reasons.values()) == {FlagReason.UNCORROBORATED}


def test_a_pure_llm_run_auto_approves_nothing():
    # every field has the model as its only reader, however right it is
    client = FakeClient(values=AGREEING | {"iban": "DE89 3704 0044 0532 0130 00"})
    res = process_document(
        make_pdf(COMPLETE_LINES), "inv.pdf", "doc-p1", llm_client=client, rules_enabled=False
    )
    assert res.decision == Decision.HUMAN_REVIEW
    assert "iban" not in res.flagged_fields, "the checksum still proves the IBAN"
    assert set(res.flagged_fields) == set(AGREEING)


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


US_AGREEING = {
    "invoice_number": "22334",
    "invoice_date": "March 25, 2025",
    "supplier_name": "Cumulus Broadcasting LLC",
    "currency": "USD",
    "total_amount": "1082.50",
    "iban": None,
    "supplier_vat_id": None,
}


def test_us_invoice_is_unapprovable_until_the_required_set_says_so(monkeypatch):
    """The same document, the same extraction, two configurations.

    Measured on DocILE: an IBAN is absent from every US invoice, so with the
    default required set no US document can ever clear review.
    """
    client = FakeClient(values=US_AGREEING)
    res = process_document(make_pdf(US_INVOICE_LINES), "us.pdf", "doc-11", llm_client=client)
    assert res.decision == Decision.HUMAN_REVIEW
    # A US layout matches one classifier keyword and lands at 0.6 — one hair
    # above the reject floor — so the type is flagged as a guess alongside the
    # two fields the document does not carry.
    assert set(res.flagged_fields) == {"<doc_type_uncertain>", "iban", "supplier_vat_id"}
    assert res.flag_reasons == {"iban": FlagReason.MISSING, "supplier_vat_id": FlagReason.MISSING}

    monkeypatch.setenv(
        "REQUIRED_FIELDS_INVOICE",
        "invoice_number,invoice_date,supplier_name,currency,total_amount",
    )
    client = FakeClient(values=US_AGREEING)
    res = process_document(make_pdf(US_INVOICE_LINES), "us.pdf", "doc-12", llm_client=client)
    assert res.decision == Decision.AUTO_APPROVE
    assert res.flagged_fields == []
    assert res.fields.iban.value is None, "still absent, just no longer disqualifying"
