"""Dead-letter queue for documents that failed processing.

n8n retries the extract call 3x and then fails the execution. The error
workflow used to only notify, so a document that failed every retry was
silently dropped — nobody could tell afterwards which documents never made it.

Rows here are that missing record: the backlog a human has to requeue.
"""

from fastapi import APIRouter, Depends, HTTPException
from models.db import DeadLetter, get_session, utcnow
from models.schemas import DeadLetterRequest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/dead-letter", tags=["dead-letter"])


@router.post("")
def record_failure(body: DeadLetterRequest, db: Session = Depends(get_session)):
    """Idempotent per execution: n8n may re-deliver the same failure."""
    existing = None
    if body.execution_id:
        existing = db.scalar(
            select(DeadLetter).where(DeadLetter.execution_id == body.execution_id)
        )
    if existing:
        return {"id": existing.id, "status": existing.status, "deduplicated": True}

    entry = DeadLetter(
        workflow_name=body.workflow_name,
        execution_id=body.execution_id,
        node_name=body.node_name,
        error_message=body.error_message,
        payload=body.payload,
        status="open",
    )
    db.add(entry)
    db.commit()
    return {"id": entry.id, "status": entry.status, "deduplicated": False}


@router.get("")
def list_failures(status: str | None = "open", db: Session = Depends(get_session)):
    query = select(DeadLetter)
    if status:
        query = query.where(DeadLetter.status == status)
    rows = db.scalars(query.order_by(DeadLetter.created_at.desc())).all()
    return {
        "total": len(rows),
        "items": [
            {
                "id": r.id,
                "workflow_name": r.workflow_name,
                "execution_id": r.execution_id,
                "node_name": r.node_name,
                "error_message": r.error_message,
                "payload": r.payload,
                "status": r.status,
                "created_at": r.created_at.isoformat(),
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
            }
            for r in rows
        ],
    }


@router.post("/{entry_id}/resolve")
def resolve(entry_id: int, db: Session = Depends(get_session)):
    entry = db.get(DeadLetter, entry_id)
    if not entry:
        raise HTTPException(404, "dead-letter entry not found")
    entry.status = "resolved"
    entry.resolved_at = utcnow()
    db.commit()
    return {"id": entry.id, "status": entry.status}


def open_count(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(DeadLetter)
            .where(DeadLetter.status == "open")
        )
        or 0
    )
