"""SROIE (ICDAR 2019 Task 3) — 626 real scanned receipts with key fields.

Real photographs and scans of till receipts: skew, creases, thermal-print
fade, and no text layer at all. That last part is the point — every one of
these takes the vision path, which the synthetic corpus only exercises with
ten JPEG-degraded pages.

Labels are `company`, `date`, `address`, `total`. Three of the four map onto
this project's invoice schema; `address` has no field here and is dropped.

Source: https://github.com/zzzDavid/ICDAR-2019-SROIE (mirror of the ICDAR 2019
competition data, which is distributed for research use).
"""

import json
from pathlib import Path

import requests

from .common import collapse_ws, fetch, normalize_amount, normalize_date, write_manifest

RAW = "https://raw.githubusercontent.com/zzzDavid/ICDAR-2019-SROIE/master/data"
SOURCE = "https://github.com/zzzDavid/ICDAR-2019-SROIE"
LICENSE = "ICDAR 2019 SROIE competition data — research use; not redistributed in this repo"
N_SAMPLES = 627

SCORED_FIELDS = {
    "supplier_name": "name",
    "invoice_date": "date",
    "total_amount": "amount",
}

NOTES = (
    "Malaysian till receipts, 2018-2019. invoice_number, supplier_vat_id, currency and iban "
    "are not labelled by this dataset and are therefore not scored. doc_type is not scored "
    "either: a receipt is neither of this project's two document types."
)


def prepare(out_dir: Path, limit: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    documents: dict[str, dict] = {}

    for i in range(N_SAMPLES):
        if len(documents) >= limit:
            break
        stem = f"{i:03d}"
        try:
            key = json.loads(fetch(f"{RAW}/key/{stem}.json", session))
        except (requests.HTTPError, json.JSONDecodeError):
            continue  # the mirror has gaps; skipping one is cheaper than mapping them

        fields = {
            "supplier_name": collapse_ws(key.get("company", "")) or None,
            "invoice_date": normalize_date(key.get("date", "")),
            "total_amount": normalize_amount(key.get("total", "")),
        }
        if any(v is None for v in fields.values()):
            continue  # a partially labelled sample would score as an extraction miss

        name = f"sroie_{stem}.jpg"
        try:
            (out_dir / name).write_bytes(fetch(f"{RAW}/img/{stem}.jpg", session))
        except requests.HTTPError:
            continue
        documents[name] = {"doc_type": None, "fields": fields}

    return write_manifest(
        out_dir,
        dataset="sroie",
        source=SOURCE,
        licence=LICENSE,
        scored_fields=SCORED_FIELDS,
        documents=documents,
        notes=NOTES,
    )
