---
status: proposed
---

# Auto-approval requires evidence, not a confidence score

A field counts toward auto-approval only if it is verified (a checksum passes) or corroborated (two independent readers produced the same value). A reader's own confidence, from either the model or the rule layer, no longer decides routing. On 25 real DocILE invoices, every wrong value the model wrote unattended carried a self-reported confidence of 0.9, the same as the right ones. The rule layer produced 3 wrong values out of 23 it read, at 0.92 to 0.95: a truncated total, a line amount taken for the total, and a character the text layer itself had misread. A higher threshold cannot separate a right value from a wrong one when both score the same.

## Considered Options

- **Raise the threshold.** Rejected: wrong values score the same as right ones, so a higher threshold blocks everything rather than the wrong values.
- **Trust deterministic rule reads and corroborate only model reads.** Rejected on the DocILE numbers above.
- **Encode evidence as confidence values** (verified = 1.0, corroborated = 0.95, everything else below 0.90). Rejected: it hides a categorical difference inside a number, which is the confusion this decision exists to remove.

## Consequences

- A rule-layer read is corroborated by asking the model for the same field in the call it already makes for the missing fields, so on real invoices the added cost is output tokens, not calls. On the synthetic corpus the 34 documents that never called the model now do. The README keeps both sets of numbers side by side, so the cost of this choice stays visible.
- A field only the model read has no second reader yet, so it cannot be auto-approved. Adding an independent second reader (OCR followed by the rule layer) is separate work.
- `AUTO_APPROVE_THRESHOLD` is removed, and setting it stops the API at startup. Ignoring it silently would let a deployment believe it had tightened a safety setting that no longer does anything.
- Only required fields need evidence. An optional field that is present but uncorroborated does not block auto-approval, but it is stored with its evidence, so downstream consumers can tell it apart from a field the document never carried.
- A reviewer's correction has evidence `reviewed`, not `verified`, because a person can mistype or misread.
