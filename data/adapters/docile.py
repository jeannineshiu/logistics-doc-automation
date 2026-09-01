"""DocILE — 6.7k annotated real business documents (Rossum, ICDAR 2023).

The closest public corpus to what this project actually processes: real
invoices with a document number, an issue date, a vendor, a total and a
currency, annotated by people who were not writing the extractor.

Access is per-token and the data is not redistributable, so this adapter reads
a local copy rather than downloading anything:

  ./download_dataset.sh SECRET_TOKEN annotated-trainval data/docile --unzip
  python data/prepare_real.py docile --limit 25

Field mapping — DocILE's 55 KILE classes onto this project's seven:

  document_id               -> invoice_number
  date_issue                -> invoice_date
  vendor_name               -> supplier_name
  vendor_tax_id             -> supplier_vat_id   (rare: 59 of 500 val documents)
  amount_total_gross        -> total_amount      (falls back to amount_due)
  metadata.currency         -> currency
  iban                      -> iban              (absent from the val split)

Two of those need explaining. The currency label comes from the metadata and
not from `currency_code_amount_due`, because that annotation's text is the
symbol printed on the page — "$" — while this project's schema stores an ISO
4217 code; scoring "$" against "USD" would measure the schema mismatch rather
than the extraction. And a field the document does not carry is simply absent
from that document's expected values: DocILE annotates what is on the page, so
per-document field sets differ, and the evaluator scores each document against
its own.

Source: https://docile.rossum.ai/ — research use, per-token access.
"""

import json
import shutil
from pathlib import Path

from .common import collapse_ws, normalize_amount, normalize_date, write_manifest

SOURCE = "https://docile.rossum.ai/"
LICENSE = "DocILE — per-token research access; not redistributed in this repo"

# DocILE labels orders, receipts and credit notes too. This project's schema is
# an invoice, so the corpus is filtered to invoices rather than scored on
# documents it was never meant to read.
INVOICE_TYPES = {"tax_invoice", "proforma"}

SCORED_FIELDS = {
    "invoice_number": "exact",
    "invoice_date": "date",
    "supplier_name": "name",
    "supplier_vat_id": "exact",
    "currency": "exact",
    "total_amount": "amount",
    "iban": "exact",
}

FIELD_SOURCES = {
    "invoice_number": ["document_id"],
    "invoice_date": ["date_issue"],
    "supplier_name": ["vendor_name"],
    "supplier_vat_id": ["vendor_tax_id"],
    "total_amount": ["amount_total_gross", "amount_due"],
    "iban": ["iban"],
}

NOTES = (
    "US business documents from the UCSF Industry Documents Library and FCC Public "
    "Inspection Files, filtered to invoices. Dates are read month-first and a date the "
    "adapter cannot normalize is left unscored for that document rather than guessed. "
    "Where a field type appears more than once on a page the topmost occurrence is taken. "
    "Many of these documents predate 2000, which the engine's date validator rejects by "
    "design — the rule layer cannot confirm a 1999 invoice date."
)


def _first(annotations: list[dict], fieldtype: str) -> str | None:
    """The topmost occurrence: DocILE repeats a field type across pages and columns."""
    hits = [f for f in annotations if f["fieldtype"] == fieldtype and f.get("text")]
    if not hits:
        return None
    hits.sort(key=lambda f: (f.get("page", 0), f["bbox"][1]))
    return hits[0]["text"]


def _expected_fields(annotation: dict) -> dict[str, str]:
    raw = annotation["field_extractions"]
    fields: dict[str, str] = {}

    for name, sources in FIELD_SOURCES.items():
        text = next((t for t in (_first(raw, s) for s in sources) if t), None)
        if text is None:
            continue
        if name == "total_amount":
            value = normalize_amount(text)
        elif name == "invoice_date":
            value = normalize_date(text, dayfirst=False)
        else:
            value = collapse_ws(text) or None
        if value is not None:
            fields[name] = value

    currency = (annotation["metadata"].get("currency") or "").upper()
    if currency and currency != "OTHER":
        fields["currency"] = currency
    return fields


def prepare(out_dir: Path, limit: int, root: Path = Path("data/docile"), split: str = "val") -> Path:
    root = Path(root)
    split_file = root / f"{split}.json"
    if not split_file.exists():
        raise SystemExit(
            f"{split_file} not found — download the dataset first:\n"
            f"  ./download_dataset.sh SECRET_TOKEN annotated-trainval {root} --unzip"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    documents: dict[str, dict] = {}

    for doc_id in json.loads(split_file.read_text()):
        if len(documents) >= limit:
            break
        annotation = json.loads((root / "annotations" / f"{doc_id}.json").read_text())
        if annotation["metadata"].get("document_type") not in INVOICE_TYPES:
            continue
        fields = _expected_fields(annotation)
        if len(fields) < 3:
            continue  # too thin to say anything about extraction quality

        name = f"docile_{doc_id}.pdf"
        shutil.copyfile(root / "pdfs" / f"{doc_id}.pdf", out_dir / name)
        documents[name] = {"doc_type": "invoice", "fields": fields}

    return write_manifest(
        out_dir,
        dataset=f"docile/{split}",
        source=SOURCE,
        licence=LICENSE,
        scored_fields=SCORED_FIELDS,
        documents=documents,
        notes=NOTES,
    )
