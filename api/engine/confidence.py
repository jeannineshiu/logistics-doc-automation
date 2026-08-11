"""Layer 3 — confidence scoring and routing decision."""

import os

from models.schemas import Decision, FieldResult

DOC_TYPE_FLOOR = 0.6


def thresholds() -> dict[str, float]:
    return {
        "auto_approve": float(os.getenv("AUTO_APPROVE_THRESHOLD", "0.90")),
        "review_floor": float(os.getenv("REVIEW_FLOOR_THRESHOLD", "0.50")),
    }


def route(
    fields: dict[str, FieldResult],
    doc_type_conf: float,
    budget_exceeded: bool = False,
) -> tuple[Decision, list[str]]:
    """Return (decision, flagged_fields)."""
    th = thresholds()

    if doc_type_conf < DOC_TYPE_FLOOR:
        return Decision.REJECT, ["<doc_type_unknown>"]

    flagged = [
        name
        for name, f in fields.items()
        if f.value is None or f.confidence < th["auto_approve"]
    ]

    if budget_exceeded:
        return Decision.HUMAN_REVIEW, ["<budget_exceeded>", *flagged]

    missing = [name for name, f in fields.items() if f.value is None]
    present_confs = [f.confidence for f in fields.values() if f.value is not None]

    if missing or (present_confs and min(present_confs) < th["review_floor"]):
        return Decision.HUMAN_REVIEW, flagged
    if all(c >= th["auto_approve"] for c in present_confs):
        return Decision.AUTO_APPROVE, []
    return Decision.HUMAN_REVIEW, flagged


def overall_confidence(fields: dict[str, FieldResult]) -> float:
    if not fields:
        return 0.0
    # missing fields count as 0 so gaps drag the score down
    return round(sum(f.confidence if f.value is not None else 0.0 for f in fields.values()) / len(fields), 4)
