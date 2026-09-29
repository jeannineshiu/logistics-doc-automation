"""Pydantic schemas shared across the extraction engine and API."""

from enum import Enum

from pydantic import BaseModel, Field, computed_field, model_validator


class DocType(str, Enum):
    INVOICE = "invoice"
    CUSTOMS_FORM = "customs_form"
    UNKNOWN = "unknown"


class ExtractionMethod(str, Enum):
    RULE = "rule"          # deterministic layer extracted it
    LLM = "llm"            # LLM layer extracted it
    HUMAN = "human"        # corrected by a reviewer
    MISSING = "missing"    # neither layer extracted it


class Evidence(str, Enum):
    """What stands behind a field value, independent of its reader's confidence.

    See CONTEXT.md. Auto-approval is decided on this (ADR-0003, proposed), because a
    reader's own confidence cannot separate a right value from a wrong one.
    """

    VERIFIED = "verified"                # a checksum passed
    CORROBORATED = "corroborated"        # two independent readers agree
    REVIEWED = "reviewed"                # a reviewer supplied or confirmed it
    UNCORROBORATED = "uncorroborated"    # one reader's word, nothing more


#: The evidence a required field needs before a document can be auto-approved.
#: Reviewed is not among them: a reviewer's judgement settles a review, it does
#: not stand in for proof when the engine decides.
APPROVABLE_EVIDENCE = frozenset({Evidence.VERIFIED, Evidence.CORROBORATED})


class FlagReason(str, Enum):
    """Why a required field is flagged, so a reviewer knows what check it needs."""

    MISSING = "missing"                  # nobody read a value
    UNCORROBORATED = "uncorroborated"    # one reader's word, nothing more
    CONFLICT = "conflict"                # independent readers disagree


class Decision(str, Enum):
    AUTO_APPROVE = "auto_approve"
    HUMAN_REVIEW = "human_review"
    REJECT = "reject"


class Status(str, Enum):
    APPROVED = "approved"
    PENDING_REVIEW = "pending_review"
    REJECTED = "rejected"


# What the routing decision means for a freshly extracted document. After a
# reviewer acts, `status` moves on but `decision` stays as the historical
# record of what the engine decided.
DECISION_STATUS: dict[Decision, Status] = {
    Decision.AUTO_APPROVE: Status.APPROVED,
    Decision.HUMAN_REVIEW: Status.PENDING_REVIEW,
    Decision.REJECT: Status.REJECTED,
}


class Candidate(BaseModel):
    """One reader's value for a field in conflict."""

    value: str
    method: ExtractionMethod


class FieldResult(BaseModel):
    value: str | None = None
    method: ExtractionMethod = ExtractionMethod.MISSING
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: Evidence | None = None
    #: Set only for a conflict: independent readers produced different values.
    #: `value` keeps the first reader's, so nothing downstream changes until a
    #: reviewer chooses; the conflict itself is what goes to review.
    candidates: list[Candidate] = []

    @model_validator(mode="after")
    def _evidence_defaults_to_the_weakest(self):
        # A value nobody vouched for is one reader's word. Defaulting here, not
        # at each reader, means a reader that forgets to say so understates its
        # evidence instead of overstating it, and a row stored before evidence
        # existed replays as uncorroborated. A missing value has nothing behind it.
        if self.value is None:
            self.evidence = None
        elif self.evidence is None:
            self.evidence = Evidence.UNCORROBORATED
        return self

    @property
    def flag_reason(self) -> FlagReason | None:
        """What stops this value counting toward auto-approval, if anything."""
        if self.value is None:
            return FlagReason.MISSING
        if self.candidates:
            return FlagReason.CONFLICT
        if self.evidence not in APPROVABLE_EVIDENCE:
            return FlagReason.UNCORROBORATED
        return None


INVOICE_FIELDS = [
    "invoice_number",
    "invoice_date",
    "supplier_name",
    "supplier_vat_id",
    "currency",
    "total_amount",
    "iban",
]

CUSTOMS_FIELDS = [
    "declaration_number",
    "hs_code",
    "country_of_origin",
    "gross_weight_kg",
    "declared_value",
    "currency",
]


class InvoiceFields(BaseModel):
    invoice_number: FieldResult = FieldResult()
    invoice_date: FieldResult = FieldResult()
    supplier_name: FieldResult = FieldResult()
    supplier_vat_id: FieldResult = FieldResult()
    currency: FieldResult = FieldResult()
    total_amount: FieldResult = FieldResult()
    iban: FieldResult = FieldResult()


class CustomsFields(BaseModel):
    declaration_number: FieldResult = FieldResult()
    hs_code: FieldResult = FieldResult()
    country_of_origin: FieldResult = FieldResult()
    gross_weight_kg: FieldResult = FieldResult()
    declared_value: FieldResult = FieldResult()
    currency: FieldResult = FieldResult()


class ExtractionResponse(BaseModel):
    document_id: str
    doc_type: DocType
    fields: InvoiceFields | CustomsFields | None
    decision: Decision
    #: Current disposition. `decision` is frozen at extraction time, so a
    #: document replayed through /extract after a reviewer approved it still
    #: reports decision=human_review — orchestration must branch on this
    #: instead, or it sends resolved documents back into the review queue.
    status: Status | None = None
    overall_confidence: float
    tokens_used: int
    cost_usd: float
    latency_ms: int
    flagged_fields: list[str] = []

    @computed_field
    @property
    def flag_reasons(self) -> dict[str, FlagReason]:
        """Why each flagged field is flagged. Document flags (`<...>`) have none.

        Derived from the fields rather than stored beside them, so a replayed
        document reports the same reasons and the two can never disagree.
        """
        if self.fields is None:
            return {}
        reasons = {}
        for name in self.flagged_fields:
            field = getattr(self.fields, name, None)
            if field is not None and field.flag_reason is not None:
                reasons[name] = field.flag_reason
        return reasons

    @model_validator(mode="after")
    def _default_status_from_decision(self):
        if self.status is None:
            self.status = DECISION_STATUS[self.decision]
        return self


class ReviewRequest(BaseModel):
    corrected_fields: dict[str, str | None]
    reviewer: str


class DeadLetterRequest(BaseModel):
    """Posted by the n8n error workflow when an execution fails for good."""

    workflow_name: str = ""
    execution_id: str = ""
    node_name: str = ""
    error_message: str = ""
    payload: dict = Field(default_factory=dict)


FIELD_NAMES: dict[DocType, list[str]] = {
    DocType.INVOICE: INVOICE_FIELDS,
    DocType.CUSTOMS_FORM: CUSTOMS_FIELDS,
}

FIELDS_MODEL: dict[DocType, type[BaseModel]] = {
    DocType.INVOICE: InvoiceFields,
    DocType.CUSTOMS_FORM: CustomsFields,
}
