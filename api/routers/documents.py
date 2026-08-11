from fastapi import APIRouter, Depends, HTTPException
from models.db import AuditLog, Document, get_session
from sqlalchemy import func, select
from sqlalchemy.orm import Session

router = APIRouter(tags=["documents"])

PAGE_SIZE = 20


@router.get("/documents")
def list_documents(
    status: str | None = None,
    doc_type: str | None = None,
    page: int = 1,
    db: Session = Depends(get_session),
):
    query = select(Document)
    if status:
        query = query.where(Document.status == status)
    if doc_type:
        query = query.where(Document.doc_type == doc_type)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(
        query.order_by(Document.created_at.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    return {
        "total": total,
        "page": page,
        "page_size": PAGE_SIZE,
        "items": [_summary(d) for d in rows],
    }


@router.get("/documents/{document_id}")
def get_document(document_id: str, db: Session = Depends(get_session)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "document not found")
    audit = db.scalars(
        select(AuditLog)
        .where(AuditLog.document_id == document_id)
        .order_by(AuditLog.created_at)
    ).all()
    return {
        **_summary(doc),
        "fields": doc.fields,
        "flagged_fields": doc.flagged_fields,
        "audit_trail": [
            {
                "actor": a.actor,
                "action": a.action,
                "detail": a.detail,
                "at": a.created_at.isoformat(),
            }
            for a in audit
        ],
    }


def _summary(d: Document) -> dict:
    return {
        "document_id": d.id,
        "filename": d.filename,
        "doc_type": d.doc_type,
        "status": d.status,
        "decision": d.decision,
        "overall_confidence": d.overall_confidence,
        "tokens_used": d.tokens_used,
        "cost_usd": d.cost_usd,
        "latency_ms": d.latency_ms,
        "created_at": d.created_at.isoformat(),
    }
