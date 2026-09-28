import json

from fastapi import APIRouter, Depends, HTTPException
from models.db import AuditLog, Document, get_session
from models.schemas import Evidence, ExtractionMethod, FieldResult, ReviewRequest
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
            # A reviewer's value is a person's judgement, not proof: its evidence
            # is `reviewed`, kept apart from `verified` because people mistype
            # and misread. Stored corrections double as a future fine-tuning /
            # few-shot dataset, which is one more reason to know which is which.
            # A reviewer who clears a value leaves nothing to vouch for: the
            # field has no evidence, like any missing field, and method=human
            # is what records that a person emptied it.
            fields[name] = FieldResult(
                value=corrected,
                method=ExtractionMethod.HUMAN,
                confidence=1.0,
                evidence=Evidence.REVIEWED,
            ).model_dump(mode="json")

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
