# Logistics Document Processing

Invoices and customs forms arrive as files and leave as structured fields. The system runs under supervised autonomy: documents it can vouch for are approved unattended, and everything else goes to a human.

## Language

### Documents

**Invoice**:
A supplier's request for payment, read for its number, date, supplier, amount and payment details.
_Avoid_: Bill, receipt (a receipt is a different document that may be mistaken for an invoice)

**Customs form**:
A declaration of goods crossing a border, read for its declaration number, HS code, origin, weight and value.
_Avoid_: Customs declaration, CN22, CN23 (those are variants of it, not synonyms)

**Required field**:
A field a document must carry before it can be auto-approved. Which fields are required is set per deployment, not fixed by the document type, because real documents of the same type differ in what they carry.
_Avoid_: Mandatory field, schema field

**Optional field**:
A field the document type defines but this deployment does not require. When absent, it neither blocks auto-approval nor appears on the reviewer's form.
_Avoid_: Nullable field, extra field

### Extraction

**Extraction method**:
Who produced a field value: the rule layer, the model, a human, or nobody because it is missing. It says who read the value, not whether the value is right.
_Avoid_: Source, extractor

### Confidence

**Evidence**:
What stands behind a field value, independent of how confident its reader claims to be: verified, corroborated, reviewed, or uncorroborated. Evidence, not field confidence, decides whether a field can be auto-approved.
_Avoid_: Confidence, trust level, certainty

**Field confidence**:
A score attached to one extracted field value, used to order and display fields for a reviewer. It does not decide routing, because the reader that produced a wrong value typically scores it as highly as a right one.
_Avoid_: Confidence (unqualified), certainty, score

**Conflict**:
Two independent readers produced different values for the same field. A conflicted field goes to review with both values shown, each labelled with who read it.
_Avoid_: Mismatch, disagreement, discrepancy

**Self-reported confidence**:
A field confidence the model assigned to its own reading. It is a claim, not evidence: on real documents, wrong values came back at the same self-reported confidence as right ones.
_Avoid_: LLM confidence, model certainty

**Verified**:
Describes a field value backed by evidence independent of whoever read it, such as a checksum that passes or totals that add up across fields. A value the rule layer found by its label or format is deterministic but not verified.
_Avoid_: Validated (when only format was checked), trusted, confirmed, deterministic (as a synonym)

**Corroborated**:
Describes a field value that two independent readers produced identically, such as the rule layer and the model, or two different models. Two reads by the same model do not corroborate each other, because they tend to repeat the same mistake. Corroborated is weaker evidence than verified.
_Avoid_: Verified (as a synonym), confirmed, double-checked, consensus

**Document-type confidence**:
How sure the system is about which kind of document it is holding. It is separate from field confidence, and too little of it means the document is rejected.
_Avoid_: Classification score, type score

**Reviewed**:
Describes a field value a reviewer supplied or confirmed. It is a person's judgement, which can be mistaken, so it is kept apart from verified.
_Avoid_: Verified (as a synonym), human-confirmed, ground truth

### Routing

**Auto-approval**:
Accepting a document unattended. A document qualifies only when every required field is verified or corroborated; its optional fields are kept with whatever evidence they have.
_Avoid_: Straight-through processing, auto-accept

**Decision**:
What the engine concluded about a document at extraction time: auto-approve, human review, or reject. It is the audit record and never changes.
_Avoid_: Outcome, result, verdict

**Status**:
A document's current disposition: approved, pending review, or rejected. It moves when a reviewer acts, and orchestration branches on it, never on the decision.
_Avoid_: State, outcome, result

**Flagged field**:
A field a reviewer must look at, because it is required and missing, uncorroborated, or in conflict.
_Avoid_: Low-confidence field, problem field

**Document flag**:
A warning about the whole document rather than one field: its type is unknown, its type is uncertain, or its extraction budget was exceeded.
_Avoid_: Sentinel, error flag

### Review

**Review**:
A reviewer's handling of a document whose status is pending review. Today a review can only end in approval; a reviewer has no way to reject a document, which is a known gap.
_Avoid_: HITL, manual check, approval

**Reviewer**:
The person who performs a review and is recorded as the author of its corrections.
_Avoid_: Operator, approver, user

**Correction**:
A field value a reviewer supplies during a review. Its extraction method is human and its evidence is reviewed.
_Avoid_: Edit, fix, override

### Failure

**Dead-letter entry**:
A record of work that could not complete and must not vanish: an orchestration run that exhausted its retries, or a correction a reviewer submitted that could not be parsed. It is open until someone resolves it.
_Avoid_: DLQ item, failed job, error
