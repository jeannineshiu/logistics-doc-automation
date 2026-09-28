# Routing is decided in the engine; orchestration branches on status

The engine returns a decision and a status alongside the extracted fields, and n8n's `Switch on Status` only fans out on `status`; it never classifies a document. The rule that separates a safe document from a doubtful one is a scored Python function with unit tests, in one place, instead of a condition duplicated across n8n nodes that nothing can test.

Orchestration must branch on `status`, not `decision`. `/extract` is idempotent by file hash, so re-sending an already-reviewed document replays its stored decision, which is still `human_review`. Branching on it sent resolved documents back to the review form, where the write-back failed with a 409 and left a spurious review task and dead-letter entry.
