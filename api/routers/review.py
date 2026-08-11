import json

from fastapi import APIRouter, Depends, HTTPException
from models.db import AuditLog, Document, get_session
from models.schemas import ReviewRequest
from sqlalchemy.orm import Session

router = APIRouter(tags=["review"])


@router.post("/review/{document_id}")
def submit_review(document_id: str, body: ReviewRequest, db: Session = Depends(get_session)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "document not found")
    if doc.status != "pending_review":
        raise HTTPException(409, f"document is not pending review (status={doc.status})")

    fields = dict(doc.fields or {})
    diff = {}
    for name, corrected in body.corrected_fields.items():
        if name not in fields:
            raise HTTPException(422, f"unknown field: {name}")
        old = fields[name].get("value")
        if old != corrected:
            diff[name] = {"from": old, "to": corrected}
            # Human-corrected values are ground truth: confidence 1.0, method stays
            # recorded in the audit log. Stored corrections double as a future
            # fine-tuning / few-shot dataset.
            fields[name] = {"value": corrected, "method": "human", "confidence": 1.0}

    doc.fields = fields
    doc.status = "approved"
    doc.flagged_fields = []
    db.add(
        AuditLog(
            document_id=document_id,
            actor=body.reviewer,
            action="review_submitted",
            detail=json.dumps(diff, ensure_ascii=False),
        )
    )
    db.commit()
    return {"document_id": document_id, "status": doc.status, "corrections": diff}
