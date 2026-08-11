"""Synthetic invoice / customs-form generator.

Creates PDFs with reportlab (3 invoice layouts EN/DE, 2 customs layouts),
plus "hard" variants (JPEG-compressed scans with noise, missing fields),
and writes ground_truth.json for evaluation.

Usage: python generate_synthetic.py [--count 50] [--out samples/]
"""

import argparse
import io
import json
import random
import string
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

random.seed(42)

SUPPLIERS = [
    ("Muster Logistik GmbH", "DE"), ("Nordwind Spedition AG", "DE"),
    ("Van Dijk Freight B.V.", "NL"), ("Alpine Cargo GmbH", "AT"),
    ("Baltic Forwarding Sp. z o.o.", "PL"), ("Riviera Shipping S.r.l.", "IT"),
]

# checksum-valid demo IBANs
IBANS = [
    "DE89370400440532013000",
    "DE75512108001245126199",
    "NL91ABNA0417164300",
    "AT611904300234573201",
    "PL61109010140000071219812874",
    "IT60X0542811101000000123456",
]

VAT_BY_COUNTRY = {
    "DE": lambda: "DE" + _digits(9),
    "NL": lambda: "NL" + _digits(9) + "B01",
    "AT": lambda: "ATU" + _digits(8),
    "PL": lambda: "PL" + _digits(10),
    "IT": lambda: "IT" + _digits(11),
}

HS_CODES = ["640411", "851713", "940360", "620342", "870899", "330499"]
ORIGINS = ["CN", "DE", "PL", "TW", "US", "IT", "NL"]


def _digits(n: int) -> str:
    return "".join(random.choices(string.digits, k=n))


def euro_fmt(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def random_date() -> str:
    y, m, d = 2025, random.randint(1, 12), random.randint(1, 28)
    return f"{y:04d}-{m:02d}-{d:02d}"


def make_invoice(idx: int) -> tuple[list[str], dict]:
    supplier, country = random.choice(SUPPLIERS)
    inv_no = f"INV-2025-{idx:05d}"
    iso_date = random_date()
    d = iso_date.split("-")
    amount = round(random.uniform(80, 9500), 2)
    iban = random.choice(IBANS)
    vat = VAT_BY_COUNTRY[country]()
    layout = idx % 3

    gt = {
        "doc_type": "invoice",
        "invoice_number": inv_no,
        "invoice_date": iso_date,
        "supplier_name": supplier,
        "supplier_vat_id": vat,
        "currency": "EUR",
        "total_amount": f"{amount:.2f}",
        "iban": iban,
    }

    if layout == 0:  # English
        lines = [
            (None, "INVOICE"),
            ("supplier_name", f"Supplier: {supplier}"),
            ("invoice_number", f"Invoice No: {inv_no}"),
            ("invoice_date", f"Invoice date: {d[2]}.{d[1]}.{d[0]}"),
            ("supplier_vat_id", f"VAT ID: {vat}"),
            (None, "Description                Qty    Price"),
            (None, "Freight services            1     " + f"{amount:.2f}"),
            ("total_amount", f"Total amount: EUR {euro_fmt(amount)}"),
            ("iban", f"IBAN: {iban}"),
        ]
    elif layout == 1:  # German
        lines = [
            (None, "RECHNUNG"),
            ("supplier_name", f"Lieferant: {supplier}"),
            ("invoice_number", f"Rechnungsnummer: {inv_no}"),
            ("invoice_date", f"Rechnungsdatum: {d[2]}.{d[1]}.{d[0]}"),
            ("supplier_vat_id", f"USt-ID: {vat}"),
            (None, "Position                   Menge  Preis"),
            (None, "Transportleistung           1     " + euro_fmt(amount)),
            ("total_amount", f"Gesamtbetrag: EUR {euro_fmt(amount)}"),
            ("iban", f"IBAN: {iban}"),
        ]
    else:  # compact / column-ish
        lines = [
            (None, "INVOICE"),
            ("supplier_name", f"Supplier: {supplier}"),
            ("invoice_number", f"Invoice Number: {inv_no}    Date of issue: {iso_date}"),
            ("supplier_vat_id", f"VAT: {vat}"),
            (None, "Charge: freight & handling"),
            ("total_amount", f"Amount due: € {euro_fmt(amount)}"),
            ("iban", f"Bank: IBAN {iban}"),
        ]
    return lines, gt


def make_customs(idx: int) -> tuple[list[str], dict]:
    decl = f"25DE{_digits(10)}"
    hs = random.choice(HS_CODES)
    origin = random.choice(ORIGINS)
    weight = round(random.uniform(0.5, 120), 1)
    value = round(random.uniform(20, 4000), 2)
    layout = idx % 2

    gt = {
        "doc_type": "customs_form",
        "declaration_number": decl,
        "hs_code": hs,
        "country_of_origin": origin,
        "gross_weight_kg": f"{weight:.2f}",
        "declared_value": f"{value:.2f}",
        "currency": "EUR",
    }

    if layout == 0:
        lines = [
            (None, "CUSTOMS DECLARATION (CN23-style)"),
            ("declaration_number", f"Declaration No: {decl}"),
            ("hs_code", f"HS Code: {hs}"),
            ("country_of_origin", f"Country of origin: {origin}"),
            ("gross_weight_kg", f"Gross weight (kg): {euro_fmt(weight)}"),
            ("declared_value", f"Declared value: EUR {euro_fmt(value)}"),
        ]
    else:
        lines = [
            (None, "ZOLLANMELDUNG / CUSTOMS"),
            ("declaration_number", f"MRN: {decl}"),
            ("hs_code", f"Warennummer (HS-Code): {hs}"),
            ("country_of_origin", f"Ursprungsland / Origin: {origin}"),
            ("gross_weight_kg", f"Bruttogewicht: {euro_fmt(weight)} kg"),
            ("declared_value", f"Warenwert / Declared value: EUR {euro_fmt(value)}"),
        ]
    return lines, gt


def render_pdf(lines: list[tuple[str | None, str]]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for _key, line in lines:
        c.drawString(50, y, line)
        y -= 22
    c.save()
    return buf.getvalue()


def make_scanned(pdf_bytes: bytes) -> bytes:
    """Rasterize + JPEG-compress + noise, wrap back into a PDF with no text layer."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    page = pdf[0]
    img = page.render(scale=150 / 72).to_pil().convert("RGB")
    pdf.close()
    # jpeg round-trip at low quality = scan artifacts
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=35)
    img = Image.open(buf).convert("RGB")
    # speckle noise
    px = img.load()
    w, h = img.size
    for _ in range(w * h // 400):
        x, y = random.randrange(w), random.randrange(h)
        px[x, y] = (random.randint(0, 80),) * 3
    out = io.BytesIO()
    img.save(out, format="PDF", resolution=150)
    return out.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--out", default="samples")
    args = ap.parse_args()

    out_dir = Path(__file__).parent / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    ground_truth = {}

    for i in range(args.count):
        if i % 3 == 2:
            lines, gt = make_customs(i)
            stem = f"customs_{i:03d}"
        else:
            lines, gt = make_invoice(i)
            stem = f"invoice_{i:03d}"

        # drop a field occasionally (hard sample: missing data)
        if i % 7 == 6:
            droppable = [j for j, (key, _) in enumerate(lines) if key]
            j = random.choice(droppable)
            key, _ = lines.pop(j)
            gt[key] = None
            # currency rides on the amount lines
            if key in ("total_amount", "declared_value") and not any(
                "EUR" in t or "€" in t for _, t in lines
            ):
                gt["currency"] = None

        pdf_bytes = render_pdf(lines)
        # every 5th document becomes a noisy scan with no text layer
        if i % 5 == 4:
            pdf_bytes = make_scanned(pdf_bytes)
            stem += "_scan"

        fname = stem + ".pdf"
        (out_dir / fname).write_bytes(pdf_bytes)
        ground_truth[fname] = gt

    gt_path = Path(__file__).parent / "ground_truth.json"
    gt_path.write_text(json.dumps(ground_truth, indent=2, ensure_ascii=False))
    print(f"wrote {args.count} documents to {out_dir}/ and ground truth to {gt_path}")


if __name__ == "__main__":
    main()
