"""LLM layer tests with a fake OpenAI client — no tokens spent."""

import json
from types import SimpleNamespace

import pytest
from engine.budget import BudgetExceeded, TokenBudget
from engine.llm_extractor import build_prompt, extract_missing_fields
from models.schemas import DocType, ExtractionMethod


class FakeClient:
    def __init__(self, payloads: list[str], tokens_per_call: int = 500):
        self._payloads = list(payloads)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._tokens = tokens_per_call

    def _create(self, **kwargs):
        self.calls += 1
        content = self._payloads.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(total_tokens=self._tokens),
        )


GOOD = json.dumps({
    "fields": {
        "supplier_name": {"value": "Acme GmbH", "confidence": 0.92, "evidence": "header"},
        "total_amount": {"value": "500.00", "confidence": 0.88, "evidence": "footer"},
    }
})


def test_extracts_missing_fields():
    budget = TokenBudget(max_tokens=8000, max_llm_calls=2)
    out = extract_missing_fields(
        [b"png"], DocType.INVOICE, ["supplier_name", "total_amount"], budget,
        client=FakeClient([GOOD]),
    )
    assert out["supplier_name"].value == "Acme GmbH"
    assert out["supplier_name"].method == ExtractionMethod.LLM
    assert out["total_amount"].confidence == 0.88
    assert budget.tokens_used == 500


def test_retries_once_on_bad_json():
    client = FakeClient(["not json {", GOOD])
    out = extract_missing_fields(
        [b"png"], DocType.INVOICE, ["supplier_name", "total_amount"],
        TokenBudget(max_tokens=8000, max_llm_calls=2), client=client,
    )
    assert client.calls == 2
    assert out["supplier_name"].value == "Acme GmbH"


def test_gives_up_after_two_bad_responses():
    out = extract_missing_fields(
        [b"png"], DocType.INVOICE, ["supplier_name"],
        TokenBudget(max_tokens=8000, max_llm_calls=2),
        client=FakeClient(["bad", "still bad"]),
    )
    assert out["supplier_name"].value is None


def test_budget_stops_retry():
    with pytest.raises(BudgetExceeded):
        extract_missing_fields(
            [b"png"], DocType.INVOICE, ["supplier_name"],
            TokenBudget(max_tokens=400, max_llm_calls=2),  # first call blows the cap
            client=FakeClient(["bad", GOOD]),
        )


def test_no_missing_fields_no_call():
    client = FakeClient([])
    out = extract_missing_fields(
        [b"png"], DocType.INVOICE, [], TokenBudget(), client=client
    )
    assert out == {}
    assert client.calls == 0


def test_null_value_marked_missing():
    payload = json.dumps({"fields": {"iban": {"value": None, "confidence": 0.0}}})
    out = extract_missing_fields(
        [b"png"], DocType.INVOICE, ["iban"], TokenBudget(), client=FakeClient([payload])
    )
    assert out["iban"].value is None
    assert out["iban"].method == ExtractionMethod.MISSING


def test_confidence_clamped():
    payload = json.dumps({"fields": {"iban": {"value": "DE00", "confidence": 3.0}}})
    out = extract_missing_fields(
        [b"png"], DocType.INVOICE, ["iban"], TokenBudget(), client=FakeClient([payload])
    )
    assert out["iban"].confidence == 1.0


def test_prompt_only_asks_missing_fields():
    p = build_prompt(DocType.INVOICE, ["iban", "supplier_name"])
    assert "iban" in p and "supplier_name" in p
    assert "invoice_number" not in p
