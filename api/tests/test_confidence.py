from engine.confidence import overall_confidence, route
from models.schemas import Decision, ExtractionMethod, FieldResult


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
