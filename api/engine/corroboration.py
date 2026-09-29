"""Corroboration — a second, independent reader for what the rule layer read.

A rule-layer value that no checksum proves is one reader's word: on real
invoices the rule layer truncated a total, took a line amount for the total and
kept a character the text layer had misread, each at a field confidence of
0.92 to 0.95 (ADR-0003, proposed). The model reads the page image rather than the
text layer, so it does not repeat those mistakes; where it produces the same
value, the value is corroborated.
"""

from models.schemas import Candidate, Evidence, FieldResult

from engine import rules


def needs_corroboration(fields: dict[str, FieldResult]) -> list[str]:
    """Rule-layer values a second reader should check: present, not verified."""
    return [
        name for name, f in fields.items()
        if f.value is not None and f.evidence != Evidence.VERIFIED
    ]


def combine(
    rule_fields: dict[str, FieldResult], model_fields: dict[str, FieldResult]
) -> dict[str, FieldResult]:
    """Merge the model's reading into the rule layer's, field by field.

    - The rule layer read nothing: the model's value fills the gap.
    - The model read nothing: the rule value stays as it was.
    - Both read the same value: it is corroborated.
    - They read different values: a conflict. Both candidates are kept,
      labelled with their reader, and the value stays uncorroborated.
    """
    out = dict(rule_fields)
    for name, second in model_fields.items():
        first = rule_fields.get(name)
        if first is None or first.value is None:
            out[name] = second
        elif second.value is None:
            continue
        elif _same(name, first.value, second.value):
            out[name] = first.model_copy(update={"evidence": Evidence.CORROBORATED})
        else:
            out[name] = first.model_copy(update={"candidates": [
                Candidate(value=first.value, method=first.method),
                Candidate(value=second.value, method=second.method),
            ]})
    return out


def _same(name: str, a: str, b: str) -> bool:
    """Equal once both readers' values are in the rule layer's comparable form.

    A different format is not a conflict: 1.234,56 and 1,234.56 are one
    amount. A value that will not normalize is compared as text, so anything
    ambiguous lands as a conflict, which a reviewer resolves, rather than as
    agreement.
    """
    ca, cb = rules.comparable(name, a), rules.comparable(name, b)
    if ca is not None and cb is not None:
        return ca == cb
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()
