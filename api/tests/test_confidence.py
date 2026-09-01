import pytest
from engine.confidence import overall_confidence, required_fields, route
from models.schemas import INVOICE_FIELDS, Decision, DocType, ExtractionMethod, FieldResult


def f(value, conf):
    return FieldResult(
        value=value,
        method=ExtractionMethod.RULE if value else ExtractionMethod.MISSING,
        confidence=conf,
    )


def test_all_high_confidence_auto_approves():
    fields = {"a": f("x", 0.95), "b": f("y", 1.0)}
    decision, flagged = route(fields, doc_type_conf=0.9)
    assert decision == Decision.AUTO_APPROVE
    assert flagged == []


def test_missing_field_forces_review():
    fields = {"a": f("x", 0.95), "b": f(None, 0.0)}
    decision, flagged = route(fields, doc_type_conf=0.9)
    assert decision == Decision.HUMAN_REVIEW
    assert "b" in flagged


def test_low_confidence_field_forces_review():
    fields = {"a": f("x", 0.95), "b": f("y", 0.3)}
    decision, flagged = route(fields, doc_type_conf=0.9)
    assert decision == Decision.HUMAN_REVIEW
    assert "b" in flagged


def test_mid_confidence_goes_to_review_not_auto():
    fields = {"a": f("x", 0.7), "b": f("y", 0.8)}
    decision, flagged = route(fields, doc_type_conf=0.9)
    assert decision == Decision.HUMAN_REVIEW
    assert set(flagged) == {"a", "b"}


def test_unknown_doc_type_rejects():
    fields = {"a": f("x", 1.0)}
    decision, flagged = route(fields, doc_type_conf=0.3)
    assert decision == Decision.REJECT
    assert flagged == ["<doc_type_unknown>"]


def test_budget_exceeded_forces_review():
    fields = {"a": f("x", 1.0)}
    decision, flagged = route(fields, doc_type_conf=0.9, budget_exceeded=True)
    assert decision == Decision.HUMAN_REVIEW
    assert "<budget_exceeded>" in flagged


def test_thresholds_from_env(monkeypatch):
    monkeypatch.setenv("AUTO_APPROVE_THRESHOLD", "0.99")
    fields = {"a": f("x", 0.95)}
    decision, _ = route(fields, doc_type_conf=0.9)
    assert decision == Decision.HUMAN_REVIEW


def test_overall_confidence_counts_missing_as_zero():
    fields = {"a": f("x", 1.0), "b": f(None, 0.0)}
    assert overall_confidence(fields) == 0.5


def test_overall_confidence_empty():
    assert overall_confidence({}) == 0.0


def test_optional_field_absent_does_not_block_auto_approve(monkeypatch):
    """A US invoice has no IBAN. Requiring one queues it for a human forever."""
    monkeypatch.setenv("REQUIRED_FIELDS_INVOICE", "invoice_number,total_amount")
    fields = {
        "invoice_number": f("INV-1", 0.95),
        "total_amount": f("10.00", 0.95),
        "iban": f(None, 0.0),
        "supplier_vat_id": f(None, 0.0),
    }
    decision, flagged = route(fields, doc_type_conf=0.9, doc_type=DocType.INVOICE)
    assert decision == Decision.AUTO_APPROVE
    assert flagged == []


def test_a_required_field_still_blocks(monkeypatch):
    monkeypatch.setenv("REQUIRED_FIELDS_INVOICE", "invoice_number,total_amount")
    fields = {"invoice_number": f("INV-1", 0.95), "total_amount": f(None, 0.0)}
    decision, flagged = route(fields, doc_type_conf=0.9, doc_type=DocType.INVOICE)
    assert decision == Decision.HUMAN_REVIEW
    assert flagged == ["total_amount"]


def test_optional_fields_are_not_flagged_for_the_reviewer(monkeypatch):
    """The review form should ask for what a reviewer can supply, and an
    optional field the document does not carry is not that."""
    monkeypatch.setenv("REQUIRED_FIELDS_INVOICE", "invoice_number,total_amount")
    fields = {
        "invoice_number": f("INV-1", 0.95),
        "total_amount": f("10.00", 0.3),
        "iban": f(None, 0.0),
    }
    _, flagged = route(fields, doc_type_conf=0.9, doc_type=DocType.INVOICE)
    assert flagged == ["total_amount"]


def test_every_field_optional_is_not_a_licence_to_approve_nothing(monkeypatch):
    """With all fields optional, an empty read used to satisfy `all([])`."""
    monkeypatch.setenv("REQUIRED_FIELDS_INVOICE", "invoice_number")
    fields = {"invoice_number": f(None, 0.0), "iban": f(None, 0.0)}
    decision, _ = route(fields, doc_type_conf=0.9, doc_type=DocType.INVOICE)
    assert decision == Decision.HUMAN_REVIEW


def test_blank_setting_reads_as_the_whole_schema(monkeypatch):
    """docker-compose passes an undefined variable through as an empty string."""
    monkeypatch.setenv("REQUIRED_FIELDS_INVOICE", "")
    assert required_fields(DocType.INVOICE) == set(INVOICE_FIELDS)


def test_unset_setting_requires_every_field(monkeypatch):
    monkeypatch.delenv("REQUIRED_FIELDS_INVOICE", raising=False)
    assert required_fields(DocType.INVOICE) == set(INVOICE_FIELDS)


def test_a_field_name_that_is_not_in_the_schema_is_refused(monkeypatch):
    """Caught at startup by main.lifespan, so a typo cannot silently drop a
    field out of the required set and start auto-approving without it."""
    monkeypatch.setenv("REQUIRED_FIELDS_INVOICE", "invoice_number,iban_typo")
    with pytest.raises(ValueError, match="iban_typo"):
        required_fields(DocType.INVOICE)


def test_an_uncertain_document_type_is_flagged_for_the_reviewer():
    fields = {"a": f("x", 0.95), "b": f(None, 0.0)}
    _, flagged = route(fields, doc_type_conf=0.65)
    assert flagged[0] == "<doc_type_uncertain>"


def test_overall_confidence_ignores_an_absent_optional_field(monkeypatch):
    monkeypatch.setenv("REQUIRED_FIELDS_INVOICE", "invoice_number")
    fields = {"invoice_number": f("INV-1", 1.0), "iban": f(None, 0.0)}
    assert overall_confidence(fields, DocType.INVOICE) == 1.0
    assert overall_confidence(fields) == 0.5, "unscoped, every field still counts"
