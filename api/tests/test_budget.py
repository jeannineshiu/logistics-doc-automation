import pytest
from engine.budget import BudgetExceeded, TokenBudget


def test_records_usage():
    b = TokenBudget(max_tokens=1000, max_llm_calls=2)
    b.record(400)
    assert b.tokens_used == 400
    assert b.llm_calls == 1


def test_call_cap_enforced():
    b = TokenBudget(max_tokens=100000, max_llm_calls=2)
    b.record(10)
    b.record(10)
    with pytest.raises(BudgetExceeded):
        b.check_can_call()


def test_token_cap_blocks_next_call():
    b = TokenBudget(max_tokens=100, max_llm_calls=10)
    b.record(100)
    with pytest.raises(BudgetExceeded):
        b.check_can_call()


def test_token_cap_exceeded_on_record():
    b = TokenBudget(max_tokens=100, max_llm_calls=10)
    with pytest.raises(BudgetExceeded):
        b.record(150)
    # usage is still recorded even though it blew the cap
    assert b.tokens_used == 150


def test_env_defaults(monkeypatch):
    monkeypatch.setenv("MAX_TOKENS_PER_DOC", "1234")
    monkeypatch.setenv("MAX_LLM_CALLS_PER_DOC", "5")
    b = TokenBudget()
    assert b.max_tokens == 1234
    assert b.max_llm_calls == 5


def test_under_budget_allows_call():
    b = TokenBudget(max_tokens=1000, max_llm_calls=2)
    b.record(500)
    b.check_can_call()  # should not raise
