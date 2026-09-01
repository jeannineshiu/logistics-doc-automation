"""End-to-end extraction pipeline: rules → LLM fallback → confidence routing."""

import time

from models.schemas import (
    FIELD_NAMES,
    FIELDS_MODEL,
    DocType,
    ExtractionResponse,
    FieldResult,
)
from openai import OpenAI

from engine import confidence as conf_mod
from engine import rules
from engine.budget import BudgetExceeded, TokenBudget
from engine.llm_extractor import extract_missing_fields
from engine.pdf_utils import load_document

# blended $/token used for reporting (input-heavy vision workload)
BLENDED_PRICE_PER_TOKEN = 3.5 / 1_000_000

# Confidence in a document type the image-only path inferred rather than read.
# INFERRED is what half a schema coming back earns; PARTIAL is what a thinner
# read earns — deliberately above conf_mod.DOC_TYPE_FLOOR, so the document
# reaches a human with the fields that *were* read attached, instead of being
# rejected and taking them with it.
INFERRED_DOC_TYPE_CONFIDENCE = 0.7
PARTIAL_DOC_TYPE_CONFIDENCE = 0.65


def process_document(
    data: bytes,
    filename: str,
    document_id: str,
    llm_client: OpenAI | None = None,
    llm_enabled: bool = True,
    rules_enabled: bool = True,
) -> ExtractionResponse:
    """Extract one document.

    `rules_enabled=False` skips the deterministic layer so every field goes to
    the LLM — the pure-LLM control group the evaluation compares against.
    """
    start = time.monotonic()
    budget = TokenBudget()

    text, page_pngs = load_document(data, filename)

    doc_type, doc_type_conf = rules.classify_doc_type(text)
    budget_exceeded = False
    fields: dict[str, FieldResult] = {}

    if doc_type in (DocType.INVOICE, DocType.CUSTOMS_FORM):
        # Layer 1: deterministic
        fields = (
            rules.run_rule_layer(text, doc_type)
            if rules_enabled
            else {name: FieldResult() for name in FIELD_NAMES[doc_type]}
        )
        # Layer 2: LLM only for the gaps
        missing = [k for k, f in fields.items() if f.value is None]
        if missing and llm_enabled:
            try:
                llm_results = extract_missing_fields(
                    page_pngs, doc_type, missing, budget, client=llm_client
                )
                fields.update(llm_results)
            except BudgetExceeded:
                budget_exceeded = True
    elif page_pngs and llm_enabled:
        # No usable text layer (scan/photo) — ask the LLM to identify + extract.
        doc_type, doc_type_conf, fields, budget_exceeded = _llm_full_extraction(
            page_pngs, budget, llm_client
        )

    decision, flagged = conf_mod.route(fields, doc_type_conf, budget_exceeded, doc_type)
    overall = conf_mod.overall_confidence(fields, doc_type)
    latency_ms = int((time.monotonic() - start) * 1000)

    fields_model = None
    if doc_type in FIELDS_MODEL:
        fields_model = FIELDS_MODEL[doc_type](**{k: v.model_dump() for k, v in fields.items()})

    return ExtractionResponse(
        document_id=document_id,
        doc_type=doc_type,
        fields=fields_model,
        decision=decision,
        overall_confidence=overall,
        tokens_used=budget.tokens_used,
        cost_usd=round(budget.tokens_used * BLENDED_PRICE_PER_TOKEN, 6),
        latency_ms=latency_ms,
        flagged_fields=flagged,
    )


def _llm_full_extraction(
    page_pngs: list[bytes],
    budget: TokenBudget,
    llm_client: OpenAI | None,
) -> tuple[DocType, float, dict[str, FieldResult], bool]:
    """Fallback for image-only documents: try invoice schema first, then customs.

    Half a schema coming back is taken as the document type. Below that, the
    best attempt is still returned rather than discarded: a receipt gives an
    invoice schema two fields of seven, which used to be rejected as an unknown
    type — throwing away a total the model had read correctly. Refusing to
    auto-approve it is right; losing it is not. Only a document that produced
    nothing at all is unknown.
    """
    best: tuple[DocType, dict[str, FieldResult], int] = (DocType.UNKNOWN, {}, 0)
    for doc_type in (DocType.INVOICE, DocType.CUSTOMS_FORM):
        try:
            results = extract_missing_fields(
                page_pngs, doc_type, FIELD_NAMES[doc_type], budget, client=llm_client
            )
        except BudgetExceeded:
            return doc_type, INFERRED_DOC_TYPE_CONFIDENCE, {}, True
        found = sum(1 for f in results.values() if f.value is not None)
        if found >= len(FIELD_NAMES[doc_type]) // 2:
            return doc_type, INFERRED_DOC_TYPE_CONFIDENCE, results, False
        if found > best[2]:
            best = (doc_type, results, found)

    if best[2]:
        return best[0], PARTIAL_DOC_TYPE_CONFIDENCE, best[1], False
    return DocType.UNKNOWN, 0.0, {}, False
