"""Layer 3 — the routing decision, made on evidence, and field confidence for display."""

import os

from models.schemas import FIELD_NAMES, Decision, DocType, FieldResult

DOC_TYPE_FLOOR = 0.6

# Below this, the document type is itself a guess rather than a reading: the
# image-only path inferred it from a partial extraction. A reviewer needs to be
# told that, because every field under it was extracted against a schema the
# engine is not sure applies.
DOC_TYPE_UNCERTAIN_BELOW = 0.7


# Settings routing no longer reads. Each once tuned a confidence threshold, and
# confidence no longer decides routing (ADR-0003, proposed): ignoring one silently would
# let a deployment believe it had tightened a safety setting that does nothing.
REMOVED_SETTINGS = {
    "AUTO_APPROVE_THRESHOLD": "auto-approval now requires every required field to be "
    "verified or corroborated, not a confidence score (ADR-0003, proposed)",
    "REVIEW_FLOOR_THRESHOLD": "any required field without evidence already goes to "
    "review, so the floor no longer changes an outcome (ADR-0003, proposed)",
}


def refuse_removed_settings() -> None:
    """Raise if a removed routing setting is set, naming what replaced it.

    Blank counts as unset: docker-compose passes an undefined variable through
    as an empty string.
    """
    present = [name for name in REMOVED_SETTINGS if os.getenv(name, "").strip()]
    if present:
        raise RuntimeError(
            "Removed settings are set: "
            + "; ".join(f"{name} — evidence replaced it: {REMOVED_SETTINGS[name]}" for name in present)
            + ". Unset them to start."
        )


def required_fields(doc_type: DocType | None = None) -> set[str] | None:
    """Which fields must be present before a document can be auto-approved.

    Defaults to every field in the schema — the behaviour this project shipped
    with, and the right one for the EU invoices it was built against, where an
    IBAN and a VAT ID are on the page.

    They are not on a US invoice. Measured against 25 real invoices from
    DocILE, `iban` was absent from 25 of 25 and `supplier_vat_id` from 24 of
    25, so every document routed to a human for fields it was never going to
    carry: a schema mismatch arriving as a review queue. Which fields a
    document class must have is a property of that class, so it belongs in
    configuration rather than compiled into the schema:

        REQUIRED_FIELDS_INVOICE=invoice_number,invoice_date,supplier_name,total_amount

    Returns None when there is no schema to check against, meaning "every
    field present" — an unknown document type has no optional fields.
    """
    if doc_type is None or doc_type not in FIELD_NAMES:
        return None
    raw = os.getenv(f"REQUIRED_FIELDS_{doc_type.name}")
    # Blank, not just unset: docker-compose passes an undefined variable through
    # as an empty string, and reading that as "nothing is required" would let a
    # document auto-approve on a single field.
    if raw is None or not raw.strip():
        return set(FIELD_NAMES[doc_type])
    names = {name.strip() for name in raw.split(",") if name.strip()}
    unknown = names - set(FIELD_NAMES[doc_type])
    if unknown:
        raise ValueError(
            f"REQUIRED_FIELDS_{doc_type.name} names fields that are not in the "
            f"{doc_type.value} schema: {sorted(unknown)}"
        )
    return names


def _is_required(name: str, required: set[str] | None) -> bool:
    return required is None or name in required


def route(
    fields: dict[str, FieldResult],
    doc_type_conf: float,
    budget_exceeded: bool = False,
    doc_type: DocType | None = None,
) -> tuple[Decision, list[str]]:
    """Return (decision, flagged_fields), decided on evidence (ADR-0003, proposed).

    A document is auto-approved when its type is established, its budget held,
    and every required field is verified or corroborated. Field confidence
    plays no part: a reader scores its wrong values as highly as its right ones.
    """
    if doc_type_conf < DOC_TYPE_FLOOR:
        return Decision.REJECT, ["<doc_type_unknown>"]

    required = required_fields(doc_type)

    # Only required fields are flagged. An optional field the document does not
    # carry is not something a reviewer can supply, and one that is present but
    # uncorroborated is kept with its evidence rather than queued for a human.
    unevidenced = [
        name for name, f in fields.items()
        if _is_required(name, required) and f.flag_reason is not None
    ]
    doc_flags = ["<doc_type_uncertain>"] if doc_type_conf < DOC_TYPE_UNCERTAIN_BELOW else []

    if budget_exceeded:
        return Decision.HUMAN_REVIEW, ["<budget_exceeded>", *doc_flags, *unevidenced]

    # No field at all is not an approval: with an optional set it is a
    # document nothing was read from.
    if unevidenced or not any(f.value is not None for f in fields.values()):
        return Decision.HUMAN_REVIEW, [*doc_flags, *unevidenced]
    return Decision.AUTO_APPROVE, []


def overall_confidence(fields: dict[str, FieldResult], doc_type: DocType | None = None) -> float:
    """Mean field confidence, with missing *required* fields counted as zero.

    An optional field the document does not carry is left out of the average
    rather than scored zero — otherwise a US invoice that read everything it
    has would report 0.6 and look doubtful on the dashboard.
    """
    required = required_fields(doc_type)
    scored = {
        name: f for name, f in fields.items() if f.value is not None or _is_required(name, required)
    }
    if not scored:
        return 0.0
    return round(
        sum(f.confidence if f.value is not None else 0.0 for f in scored.values()) / len(scored), 4
    )
