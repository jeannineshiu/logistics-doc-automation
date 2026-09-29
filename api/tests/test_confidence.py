import pytest
from engine.confidence import overall_confidence, refuse_removed_settings, required_fields, route
from models.schemas import (
    INVOICE_FIELDS,
    Candidate,
    Decision,
    DocType,
    Evidence,
    ExtractionMethod,
    FieldResult,
)

V, C, U = Evidence.VERIFIED, Evidence.CORROBORATED, Evidence.UNCORROBORATED


def f(value, evidence=None, conf=0.95):
    return FieldResult(
        value=value,
        method=ExtractionMethod.RULE if value else ExtractionMethod.MISSING,
        confidence=conf if value else 0.0,
        evidence=evidence,
    )


def conflict(rule_value, model_value):
    return f(rule_value, U).model_copy(update={"candidates": [
        Candidate(value=rule_value, method=ExtractionMethod.RULE),
        Candidate(value=model_value, method=ExtractionMethod.LLM),
    ]})


def test_verified_and_corroborated_fields_auto_approve():
    fields = {"a": f("x", V), "b": f("y", C)}
    decision, flagged = route(fields, doc_type_conf=0.9)
    assert decision == Decision.AUTO_APPROVE
    assert flagged == []


def test_missing_field_forces_review():
    fields = {"a": f("x", C), "b": f(None)}
    decision, flagged = route(fields, doc_type_conf=0.9)
    assert decision == Decision.HUMAN_REVIEW
    assert flagged == ["b"]


def test_an_uncorroborated_field_forces_review_however_confident_its_reader():
    fields = {"a": f("x", C), "b": f("y", U, conf=1.0)}
    decision, flagged = route(fields, doc_type_conf=0.9)
    assert decision == Decision.HUMAN_REVIEW
    assert flagged == ["b"]


def test_a_conflict_forces_review():
    fields = {"a": f("x", C), "total": conflict("121.72", "243.44")}
    decision, flagged = route(fields, doc_type_conf=0.9)
    assert decision == Decision.HUMAN_REVIEW
    assert flagged == ["total"]


def test_field_confidence_does_not_hold_back_an_evidenced_field():
    fields = {"a": f("x", C, conf=0.1), "b": f("y", V, conf=0.3)}
    decision, _ = route(fields, doc_type_conf=0.9)
    assert decision == Decision.AUTO_APPROVE


def test_a_reviewed_value_is_not_evidence_the_engine_approves_on():
    fields = {"a": f("x", C), "b": f("y", Evidence.REVIEWED)}
    decision, flagged = route(fields, doc_type_conf=0.9)
    assert decision == Decision.HUMAN_REVIEW
    assert flagged == ["b"]


def test_unknown_doc_type_rejects():
    fields = {"a": f("x", V)}
    decision, flagged = route(fields, doc_type_conf=0.3)
    assert decision == Decision.REJECT
    assert flagged == ["<doc_type_unknown>"]


def test_budget_exceeded_forces_review():
    fields = {"a": f("x", V)}
    decision, flagged = route(fields, doc_type_conf=0.9, budget_exceeded=True)
    assert decision == Decision.HUMAN_REVIEW
    assert flagged == ["<budget_exceeded>"]


@pytest.mark.parametrize("name", ["AUTO_APPROVE_THRESHOLD", "REVIEW_FLOOR_THRESHOLD"])
def test_a_removed_threshold_setting_is_refused(monkeypatch, name):
    monkeypatch.setenv(name, "0.99")
    with pytest.raises(RuntimeError, match=f"{name}.*evidence replaced it"):
        refuse_removed_settings()


def test_a_blank_removed_setting_reads_as_unset(monkeypatch):
    """docker-compose passes an undefined variable through as an empty string."""
    monkeypatch.setenv("AUTO_APPROVE_THRESHOLD", "")
    refuse_removed_settings()


def test_an_absent_optional_field_does_not_block_auto_approve(monkeypatch):
    """A US invoice has no IBAN. Requiring one queues it for a human forever."""
    monkeypatch.setenv("REQUIRED_FIELDS_INVOICE", "invoice_number,total_amount")
    fields = {
        "invoice_number": f("INV-1", C),
        "total_amount": f("10.00", C),
        "iban": f(None),
        "supplier_vat_id": f(None),
    }
    decision, flagged = route(fields, doc_type_conf=0.9, doc_type=DocType.INVOICE)
    assert decision == Decision.AUTO_APPROVE
    assert flagged == []


def test_an_uncorroborated_or_conflicted_optional_field_does_not_block_auto_approve(monkeypatch):
    monkeypatch.setenv("REQUIRED_FIELDS_INVOICE", "invoice_number,total_amount")
    fields = {
        "invoice_number": f("INV-1", C),
        "total_amount": f("10.00", V),
        "currency": f("EUR", U),
        "supplier_vat_id": conflict("DE1", "DE7"),
    }
    decision, flagged = route(fields, doc_type_conf=0.9, doc_type=DocType.INVOICE)
    assert decision == Decision.AUTO_APPROVE
    assert flagged == []


def test_a_required_field_still_blocks(monkeypatch):
    monkeypatch.setenv("REQUIRED_FIELDS_INVOICE", "invoice_number,total_amount")
    fields = {"invoice_number": f("INV-1", C), "total_amount": f(None)}
    decision, flagged = route(fields, doc_type_conf=0.9, doc_type=DocType.INVOICE)
    assert decision == Decision.HUMAN_REVIEW
    assert flagged == ["total_amount"]


def test_optional_fields_are_not_flagged_for_the_reviewer(monkeypatch):
    """The review form should ask for what a reviewer needs to check, and an
    optional field is not that, whether absent or unproven."""
    monkeypatch.setenv("REQUIRED_FIELDS_INVOICE", "invoice_number,total_amount")
    fields = {
        "invoice_number": f("INV-1", C),
        "total_amount": f("10.00", U),
        "currency": f("EUR", U),
        "iban": f(None),
    }
    _, flagged = route(fields, doc_type_conf=0.9, doc_type=DocType.INVOICE)
    assert flagged == ["total_amount"]


def test_every_field_optional_is_not_a_licence_to_approve_nothing(monkeypatch):
    """With all fields optional, an empty read used to satisfy `all([])`."""
    monkeypatch.setenv("REQUIRED_FIELDS_INVOICE", "invoice_number")
    fields = {"invoice_number": f(None), "iban": f(None)}
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
    fields = {"a": f("x", C), "b": f(None)}
    _, flagged = route(fields, doc_type_conf=0.65)
    assert flagged == ["<doc_type_uncertain>", "b"]


def test_overall_confidence_counts_missing_as_zero():
    fields = {"a": f("x", conf=1.0), "b": f(None)}
    assert overall_confidence(fields) == 0.5


def test_overall_confidence_empty():
    assert overall_confidence({}) == 0.0


def test_overall_confidence_ignores_an_absent_optional_field(monkeypatch):
    monkeypatch.setenv("REQUIRED_FIELDS_INVOICE", "invoice_number")
    fields = {"invoice_number": f("INV-1", conf=1.0), "iban": f(None)}
    assert overall_confidence(fields, DocType.INVOICE) == 1.0
    assert overall_confidence(fields) == 0.5, "unscoped, every field still counts"
