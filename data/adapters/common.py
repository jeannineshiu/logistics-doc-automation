"""Shared plumbing for the real-corpus adapters.

An adapter turns a public dataset into two things the evaluator understands:
a directory of document files, and a `manifest.json` naming which of this
project's fields that dataset actually labels.

Real corpora label a *subset* of our schema — SROIE knows a merchant name, a
date and a total, and nothing about an IBAN or a VAT id. Scoring the unlabeled
fields as wrong would report an accuracy that says more about the dataset than
about the extractor, so the manifest carries `scored_fields` and the evaluator
ignores everything else.
"""

import json
import re
from pathlib import Path

import requests
from dateutil import parser as dateparser

MANIFEST_VERSION = 1
TIMEOUT = 30

# Comparators the evaluator supports, keyed by field in `scored_fields`.
COMPARATORS = ("exact", "name", "date", "amount")


def normalize_amount(raw: str) -> str | None:
    """Mirror of `engine.rules.normalize_amount`.

    Ground truth has to be normalized the same way the engine normalizes what
    it extracts, or the comparison measures formatting rather than accuracy.
    Duplicated rather than imported because the adapters run outside the api/
    package; `test_real_adapters.py` asserts the two stay in agreement.
    """
    s = raw.strip().replace(" ", "").replace(" ", "")
    s = re.sub(r"[^\d.,-]", "", s)
    if not re.search(r"\d", s):
        return None
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    if last_comma > last_dot:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        value = float(s)
    except ValueError:
        return None
    if value < 0:
        return None
    return f"{value:.2f}"


def normalize_date(raw: str, dayfirst: bool = True) -> str | None:
    """Parse a dataset's date string to ISO.

    Day-first by default — SROIE and CORD are not US corpora — and month-first
    for DocILE, where 06/07/99 is the 7th of June. The convention belongs to
    the dataset, so the adapter passes it rather than the parser guessing.
    """
    try:
        return dateparser.parse(raw.strip(), dayfirst=dayfirst, fuzzy=False).date().isoformat()
    except (ValueError, OverflowError, TypeError):
        return None


def collapse_ws(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip()


def fetch(url: str, session: requests.Session | None = None) -> bytes:
    s = session or requests
    r = s.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def write_manifest(
    out_dir: Path,
    *,
    dataset: str,
    source: str,
    licence: str,
    scored_fields: dict[str, str],
    documents: dict[str, dict],
    notes: str = "",
) -> Path:
    unknown = set(scored_fields.values()) - set(COMPARATORS)
    if unknown:
        raise ValueError(f"unknown comparator(s): {sorted(unknown)}")
    path = out_dir / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "manifest_version": MANIFEST_VERSION,
                "dataset": dataset,
                "source": source,
                "license": licence,
                "notes": notes,
                "scored_fields": scored_fields,
                "documents": documents,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    return path
