from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from models.db import Document, get_session
from sqlalchemy import func, select
from sqlalchemy.orm import Session

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(db: Session = Depends(get_session)):
    """Prometheus text exposition format."""
    total = db.scalar(select(func.count()).select_from(Document)) or 0
    by_decision = dict(
        db.execute(
            select(Document.decision, func.count()).group_by(Document.decision)
        ).all()
    )
    tokens = db.scalar(select(func.coalesce(func.sum(Document.tokens_used), 0)))
    cost = db.scalar(select(func.coalesce(func.sum(Document.cost_usd), 0.0)))
    review_rate = (
        (total - by_decision.get("auto_approve", 0)) / total if total else 0.0
    )

    lines = [
        "# HELP documents_processed_total Total documents processed.",
        "# TYPE documents_processed_total counter",
        f"documents_processed_total {total}",
        "# HELP documents_by_decision_total Documents by routing decision.",
        "# TYPE documents_by_decision_total counter",
    ]
    for decision in ("auto_approve", "human_review", "reject"):
        lines.append(
            f'documents_by_decision_total{{decision="{decision}"}} {by_decision.get(decision, 0)}'
        )
    lines += [
        "# HELP human_review_rate Share of documents not auto-approved.",
        "# TYPE human_review_rate gauge",
        f"human_review_rate {review_rate:.4f}",
        "# HELP tokens_used_total Total LLM tokens consumed.",
        "# TYPE tokens_used_total counter",
        f"tokens_used_total {tokens}",
        "# HELP llm_cost_usd_total Total estimated LLM cost in USD.",
        "# TYPE llm_cost_usd_total counter",
        f"llm_cost_usd_total {cost:.6f}",
    ]
    return "\n".join(lines) + "\n"
