"""Field comparators.

The synthetic corpus can be scored with string equality because the generator
wrote both sides. A real corpus cannot: its ground truth was typed by human
annotators reading a photograph, so `BOOK TA .K (TAMAN DAYA) SDN BHD` comes
back as `BOOK TA.K (TAMAN DAYA) SDN BHD` and a date written `12-01-19` has to
be compared against an ISO string.

Each comparator here is a deliberate scoring decision, and every one of them
can flatter the extractor if it is too loose — which is why `name` is the only
fuzzy one, it has a stated threshold, and amounts report a near-miss instead of
quietly widening the tolerance.
"""

import difflib
import re
from dataclasses import dataclass

from dateutil import parser as dateparser

NAME_RATIO = 0.85       # difflib similarity above which two names are the same merchant
AMOUNT_EPS = 0.005      # half a cent
SCALE_FACTOR = 1000     # thousands-separator locales: 60.000 IDR read as 60.00


@dataclass(frozen=True)
class Verdict:
    ok: bool
    note: str = ""


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def _clean_name(value) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(value))).strip().casefold()


def _to_float(value) -> float | None:
    try:
        return float(re.sub(r"[^\d.-]", "", str(value)))
    except ValueError:
        return None


def compare(kind: str, got, want) -> Verdict:
    if got is None or want is None:
        return Verdict(got is None and want is None, "both missing" if got is want else "missing")
    return _COMPARATORS[kind](got, want)


def _exact(got, want) -> Verdict:
    return Verdict(_clean(got) == _clean(want))


def _name(got, want) -> Verdict:
    a, b = _clean_name(got), _clean_name(want)
    if a == b:
        return Verdict(True)
    if a and b and (a in b or b in a):
        return Verdict(True, "substring")
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return Verdict(ratio >= NAME_RATIO, f"similarity {ratio:.2f}")


def _date(got, want) -> Verdict:
    parsed = []
    for value in (got, want):
        try:
            parsed.append(dateparser.parse(str(value), dayfirst=True, fuzzy=False).date())
        except (ValueError, OverflowError, TypeError):
            return Verdict(False, "unparseable")
    return Verdict(parsed[0] == parsed[1])


def _amount(got, want) -> Verdict:
    a, b = _to_float(got), _to_float(want)
    if a is None or b is None:
        return Verdict(False, "unparseable")
    if abs(a - b) <= AMOUNT_EPS:
        return Verdict(True)
    for scale in (SCALE_FACTOR, 1 / SCALE_FACTOR):
        if abs(a - b * scale) <= AMOUNT_EPS * max(1, abs(scale)):
            return Verdict(False, f"scale x{SCALE_FACTOR}")
    return Verdict(False)


_COMPARATORS = {"exact": _exact, "name": _name, "date": _date, "amount": _amount}
