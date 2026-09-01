"""CORD v2 — 1,000 real Indonesian receipts, line-item annotated.

Pulled through the Hugging Face datasets-server rows API rather than the
`datasets` library, so preparing a corpus needs nothing beyond `requests`.

Only `total.total_price` maps onto this project's schema. CORD is here for the
layout, not the field coverage: dense line items, stamped and folded paper,
and a locale whose amounts are written `60.000` for sixty thousand.

Source: https://huggingface.co/datasets/naver-clova-ix/cord-v2 (CC BY 4.0).
"""

import json
import re
from pathlib import Path

import requests

from .common import fetch, write_manifest

ROWS_API = "https://datasets-server.huggingface.co/rows"
DATASET = "naver-clova-ix/cord-v2"
SOURCE = "https://huggingface.co/datasets/naver-clova-ix/cord-v2"
LICENSE = "CC BY 4.0 — not redistributed in this repo"
PAGE = 100

SCORED_FIELDS = {"total_amount": "amount"}


def _rupiah(raw: str) -> str | None:
    """Read a CORD price as whole rupiah.

    The generic normalizer reads the rightmost separator as a decimal point,
    which turns '60.000' into 60.00 — and then scores the model's correct
    60000 as a miss. It measured the ground truth, not the extraction: on the
    first 15 test receipts, 10 of the 11 apparent errors were this and nothing
    else. Rupiah amounts on a receipt are integers, and CORD writes the
    separator both ways ('60.000' and '91,000') within one corpus, so the only
    consistent reading is to drop every separator.
    """
    digits = re.sub(r"[^\d]", "", raw)
    return f"{int(digits)}.00" if digits else None

NOTES = (
    "Indonesian receipts. Only total_amount is labelled in terms this schema shares; "
    "supplier_name, invoice_number, invoice_date, supplier_vat_id, currency and iban are not "
    "scored, and neither is doc_type. Amounts are read as whole rupiah: CORD writes sixty "
    "thousand as '60.000' and ninety-one thousand as '91,000' in the same corpus, and rupiah "
    "has no sub-unit in practice, so every separator is a thousands separator."
)


def prepare(out_dir: Path, limit: int, split: str = "test") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    documents: dict[str, dict] = {}
    offset = 0

    while len(documents) < limit:
        r = session.get(
            ROWS_API,
            params={
                "dataset": DATASET,
                "config": "default",
                "split": split,
                "offset": offset,
                "length": min(PAGE, limit - len(documents)),
            },
            timeout=60,
        )
        r.raise_for_status()
        page = r.json()
        rows = page.get("rows", [])
        if not rows:
            break

        for item in rows:
            row = item["row"]
            gt = json.loads(row["ground_truth"]).get("gt_parse", {})
            total = _rupiah(str(gt.get("total", {}).get("total_price", "")))
            if total is None:
                continue
            name = f"cord_{split}_{item['row_idx']:03d}.jpg"
            (out_dir / name).write_bytes(fetch(row["image"]["src"], session))
            documents[name] = {"doc_type": None, "fields": {"total_amount": total}}

        offset += len(rows)
        if offset >= page.get("num_rows_total", offset):
            break

    return write_manifest(
        out_dir,
        dataset=f"cord-v2/{split}",
        source=SOURCE,
        licence=LICENSE,
        scored_fields=SCORED_FIELDS,
        documents=documents,
        notes=NOTES,
    )
