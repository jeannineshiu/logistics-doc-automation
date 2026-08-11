"""Per-document token/cost budget — hard caps enforced in code, not prompts."""

import os


class BudgetExceeded(Exception):
    pass


class TokenBudget:
    """Tracks token usage for one document and aborts when a cap is hit."""

    def __init__(
        self,
        max_tokens: int | None = None,
        max_llm_calls: int | None = None,
    ):
        self.max_tokens = max_tokens or int(os.getenv("MAX_TOKENS_PER_DOC", "8000"))
        self.max_llm_calls = max_llm_calls or int(os.getenv("MAX_LLM_CALLS_PER_DOC", "2"))
        self.tokens_used = 0
        self.llm_calls = 0

    def check_can_call(self) -> None:
        """Raise before an LLM call if either cap is already exhausted."""
        if self.llm_calls >= self.max_llm_calls:
            raise BudgetExceeded(
                f"LLM call cap reached ({self.llm_calls}/{self.max_llm_calls})"
            )
        if self.tokens_used >= self.max_tokens:
            raise BudgetExceeded(
                f"token cap reached ({self.tokens_used}/{self.max_tokens})"
            )

    def record(self, tokens: int) -> None:
        """Record usage after a call; raise if the cap was blown."""
        self.llm_calls += 1
        self.tokens_used += tokens
        if self.tokens_used > self.max_tokens:
            raise BudgetExceeded(
                f"token cap exceeded ({self.tokens_used}/{self.max_tokens})"
            )
