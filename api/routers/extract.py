import hashlib
import os
import uuid

from engine.pipeline import process_document
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from models.db import AuditLog, Document, get_session
from models.schemas import Decision, ExtractionResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(tags=["extract"])

ALLOWED_EXT = (".pdf", ".jpg", ".jpeg", ".png")

DECISION_STATUS = {
    Decision.AUTO_APPROVE: "approved",
    Decision.HUMAN_REVIEW: "pending_review",
    Decision.REJECT: "rejected",
}


@router.post("/extract", response_model=ExtractionResponse)
async def extract(file: UploadFile, db: Session = Depends(get_session)):
    if not file.filename or not file.filename.lower().endswith(ALLOWED_EXT):
        raise HTTPException(415, "unsupported file type; use pdf/jpg/png")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")

    # Idempotency: the same file re-sent returns the stored result, no re-extraction.
    file_hash = hashlib.sha256(data).hexdigest()
    existing = db.scalar(select(Document).where(Document.file_hash == file_hash))
    if existing:
        return _to_response(existing)

    document_id = str(uuid.uuid4())
    result = process_document(
        data,
        file.filename,
        document_id,
        llm_enabled=os.getenv("LLM_ENABLED", "1") == "1",
        rules_enabled=os.getenv("RULE_LAYER_ENABLED", "1") == "1",
    )

    doc = Document(
        id=document_id,
        file_hash=file_hash,
        filename=file.filename,
        doc_type=result.doc_type.value,
        status=DECISION_STATUS[result.decision],
        decision=result.decision.value,
        fields=result.fields.model_dump() if result.fields else {},
        overall_confidence=result.overall_confidence,
        tokens_used=result.tokens_used,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
        flagged_fields=result.flagged_fields,
    )
    db.add(doc)
    db.add(
        AuditLog(
            document_id=document_id,
            actor="system",
            action=f"extracted:{result.decision.value}",
            detail=f"confidence={result.overall_confidence} tokens={result.tokens_used} "
            f"flagged={result.flagged_fields}",
        )
    )
    db.commit()
    return result


def _to_response(doc: Document) -> ExtractionResponse:
    return ExtractionResponse(
        document_id=doc.id,
        doc_type=doc.doc_type,
        fields=doc.fields or None,
        decision=doc.decision,
        overall_confidence=doc.overall_confidence,
        tokens_used=doc.tokens_used,
        cost_usd=doc.cost_usd,
        latency_ms=doc.latency_ms,
        flagged_fields=doc.flagged_fields or [],
    )
