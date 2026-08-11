"""Layer 1 — deterministic extraction.

Runs on the PDF text layer (no OCR, no LLM). Fields with a fixed format are
extracted with regexes and validated; a validated hit gets high confidence
and never reaches the LLM.
"""

import re
from datetime import UTC, datetime

from dateutil import parser as dateparser
from models.schemas import DocType, ExtractionMethod, FieldResult
from schwifty import IBAN
from schwifty.exceptions import SchwiftyException

ISO_4217 = {
    "EUR", "USD", "GBP", "CHF", "PLN", "SEK", "DKK", "NOK", "CZK", "HUF",
    "CNY", "JPY", "TWD", "HKD", "SGD", "AUD", "CAD", "RON", "BGN", "TRY",
}

ISO_3166_A2 = {
    "AT", "BE", "BG", "CH", "CN", "CZ", "DE", "DK", "EE", "ES", "FI", "FR",
    "GB", "GR", "HK", "HR", "HU", "IE", "IT", "JP", "KR", "LT", "LU", "LV",
    "MT", "NL", "NO", "PL", "PT", "RO", "SE", "SG", "SI", "SK", "TW", "US",
}

# EU VAT ID formats (subset, most common in the demo corpus)
VAT_PATTERNS = {
    "DE": r"DE\d{9}",
    "AT": r"ATU\d{8}",
    "NL": r"NL\d{9}B\d{2}",
    "FR": r"FR[A-Z0-9]{2}\d{9}",
    "PL": r"PL\d{10}",
    "IT": r"IT\d{11}",
    "ES": r"ES[A-Z0-9]\d{7}[A-Z0-9]",
    "GB": r"GB\d{9}",
}

HS_CODE_CHAPTER_MAX = 97  # valid HS chapters are 01–97

CONF_VALIDATED = 1.0      # passed a mathematical/checksum validation
CONF_FORMAT_OK = 0.95     # matched a strict format pattern
CONF_HEURISTIC = 0.92     # matched a labeled pattern + normalized cleanly
CONF_WEAK = 0.80          # looser heuristic — always routes to review


# ---------------------------------------------------------------- doc type

def classify_doc_type(text: str) -> tuple[DocType, float]:
    """Keyword-vote classifier over the text layer."""
    t = text.lower()
    invoice_hits = sum(k in t for k in [
        "invoice", "rechnung", "invoice number", "rechnungsnummer",
        "amount due", "total due", "iban", "vat", "ust-id",
    ])
    customs_hits = sum(k in t for k in [
        "customs", "zoll", "customs declaration", "hs code", "hs-code",
        "country of origin", "declaration number", "gross weight",
        "declared value", "cn22", "cn23", "tariff",
    ])
    if invoice_hits == 0 and customs_hits == 0:
        return DocType.UNKNOWN, 0.0
    total = invoice_hits + customs_hits
    if customs_hits > invoice_hits:
        return DocType.CUSTOMS_FORM, min(1.0, 0.5 + 0.1 * customs_hits) * (customs_hits / total)
    return DocType.INVOICE, min(1.0, 0.5 + 0.1 * invoice_hits) * (invoice_hits / total)


# ---------------------------------------------------------------- helpers

def normalize_amount(raw: str) -> str | None:
    """Normalize '1.234,56' / '1,234.56' / '1234.56' to '1234.56'."""
    s = raw.strip().replace(" ", "").replace(" ", "")
    s = re.sub(r"[^\d.,-]", "", s)
    if not re.search(r"\d", s):
        return None
    # decide decimal separator: the rightmost of . or , if followed by 1-2 digits
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    if last_comma > last_dot:
        # European: comma decimal, dots are thousands
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


def validate_iban(candidate: str) -> str | None:
    try:
        return str(IBAN(candidate.replace(" ", "")))
    except SchwiftyException:
        return None


def parse_date(raw: str) -> str | None:
    """Parse a date string, reject dates in the future or before 2000."""
    try:
        d = dateparser.parse(raw, dayfirst=True, fuzzy=False).date()
    except (ValueError, OverflowError):
        return None
    if d > datetime.now(UTC).date() or d.year < 2000:
        return None
    return d.isoformat()


def validate_hs_code(code: str) -> bool:
    if not re.fullmatch(r"\d{6,10}", code):
        return False
    return 1 <= int(code[:2]) <= HS_CODE_CHAPTER_MAX


# ---------------------------------------------------------------- field extractors

_LABEL = r"[:\s]*"

def _search(pattern: str, text: str, group: int = 1) -> str | None:
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(group).strip() if m else None


def extract_invoice_number(text: str) -> FieldResult:
    for pat in [
        rf"(?:invoice\s*(?:no\.?|number|#)|rechnungs?(?:nummer|-?nr\.?)){_LABEL}([A-Z0-9][A-Z0-9\-/]{{2,24}})",
        r"\b(INV[-/]?\d{4,12})\b",
    ]:
        val = _search(pat, text)
        if val:
            return FieldResult(value=val, method=ExtractionMethod.RULE, confidence=CONF_FORMAT_OK)
    return FieldResult()


def extract_invoice_date(text: str) -> FieldResult:
    raw = _search(
        rf"(?:invoice\s*date|date\s*of\s*issue|rechnungsdatum|datum){_LABEL}"
        r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\.?\s+[A-Za-zäöü]+\s+\d{4})",
        text,
    )
    if raw:
        parsed = parse_date(raw)
        if parsed:
            return FieldResult(value=parsed, method=ExtractionMethod.RULE, confidence=CONF_FORMAT_OK)
    return FieldResult()


def extract_iban(text: str) -> FieldResult:
    for m in re.finditer(r"\b[A-Z]{2}\d{2}[ ]?(?:[A-Z0-9][ ]?){10,30}\b", text):
        valid = validate_iban(m.group(0))
        if valid:
            # checksum passed -> mathematically validated
            return FieldResult(value=valid, method=ExtractionMethod.RULE, confidence=CONF_VALIDATED)
    return FieldResult()


def extract_vat_id(text: str) -> FieldResult:
    for pat in VAT_PATTERNS.values():
        val = _search(rf"\b({pat})\b", text)
        if val:
            return FieldResult(value=val, method=ExtractionMethod.RULE, confidence=CONF_FORMAT_OK)
    return FieldResult()


def extract_currency(text: str) -> FieldResult:
    for code in ISO_4217:
        if re.search(rf"\b{code}\b", text):
            return FieldResult(value=code, method=ExtractionMethod.RULE, confidence=CONF_FORMAT_OK)
    if "€" in text:
        return FieldResult(value="EUR", method=ExtractionMethod.RULE, confidence=CONF_HEURISTIC)
    if "$" in text:
        return FieldResult(value="USD", method=ExtractionMethod.RULE, confidence=0.6)
    return FieldResult()


_AMOUNT = r"([\d.,  ]*\d(?:[.,]\d{1,2})?)"

def extract_total_amount(text: str) -> FieldResult:
    for pat in [
        rf"(?:total\s*(?:amount|due)?|amount\s*due|gesamtbetrag|summe|grand\s*total)\s*[:\s]\s*(?:EUR|USD|GBP|CHF|€|\$)?\s*{_AMOUNT}",
        rf"(?:EUR|€)\s*{_AMOUNT}\s*(?:total|gesamt)",
    ]:
        raw = _search(pat, text)
        if raw:
            norm = normalize_amount(raw)
            if norm and float(norm) > 0:
                return FieldResult(value=norm, method=ExtractionMethod.RULE, confidence=CONF_HEURISTIC)
    return FieldResult()


def extract_declaration_number(text: str) -> FieldResult:
    val = _search(
        rf"(?:declaration\s*(?:no\.?|number|#)|anmeldenummer|mrn){_LABEL}([A-Z0-9][A-Z0-9\-/]{{4,24}})",
        text,
    )
    if val:
        return FieldResult(value=val, method=ExtractionMethod.RULE, confidence=CONF_FORMAT_OK)
    return FieldResult()


def extract_hs_code(text: str) -> FieldResult:
    raw = _search(
        rf"(?:hs[\s-]?code|tariff\s*(?:no\.?|code)|warennummer)\s*(?:\([^)]*\))?{_LABEL}(\d{{6,10}})",
        text,
    )
    if raw and validate_hs_code(raw):
        return FieldResult(value=raw, method=ExtractionMethod.RULE, confidence=CONF_FORMAT_OK)
    return FieldResult()


def extract_country_of_origin(text: str) -> FieldResult:
    raw = _search(rf"(?:country\s*of\s*origin|ursprungsland|origin){_LABEL}([A-Z]{{2}})\b", text)
    if raw and raw.upper() in ISO_3166_A2:
        return FieldResult(value=raw.upper(), method=ExtractionMethod.RULE, confidence=CONF_FORMAT_OK)
    return FieldResult()


def extract_gross_weight(text: str) -> FieldResult:
    raw = _search(rf"(?:gross\s*weight|rohgewicht|bruttogewicht)\s*(?:\(kg\))?{_LABEL}{_AMOUNT}\s*(?:kg)?", text)
    if raw:
        norm = normalize_amount(raw)
        if norm:
            return FieldResult(value=norm, method=ExtractionMethod.RULE, confidence=CONF_HEURISTIC)
    return FieldResult()


def extract_declared_value(text: str) -> FieldResult:
    raw = _search(
        rf"(?:declared\s*value|total\s*value|warenwert)\s*[:\s]\s*(?:EUR|USD|GBP|€|\$)?\s*{_AMOUNT}",
        text,
    )
    if raw:
        norm = normalize_amount(raw)
        if norm and float(norm) > 0:
            return FieldResult(value=norm, method=ExtractionMethod.RULE, confidence=CONF_HEURISTIC)
    return FieldResult()


def extract_supplier_name(text: str) -> FieldResult:
    # Company names have no fixed format — try a labeled line, else leave to LLM.
    raw = _search(r"(?:supplier|seller|from|lieferant)\s*:\s*([^\n]{3,60})", text)
    if raw:
        return FieldResult(value=raw.strip(), method=ExtractionMethod.RULE, confidence=0.90)
    return FieldResult()


INVOICE_EXTRACTORS = {
    "invoice_number": extract_invoice_number,
    "invoice_date": extract_invoice_date,
    "supplier_name": extract_supplier_name,
    "supplier_vat_id": extract_vat_id,
    "currency": extract_currency,
    "total_amount": extract_total_amount,
    "iban": extract_iban,
}

CUSTOMS_EXTRACTORS = {
    "declaration_number": extract_declaration_number,
    "hs_code": extract_hs_code,
    "country_of_origin": extract_country_of_origin,
    "gross_weight_kg": extract_gross_weight,
    "declared_value": extract_declared_value,
    "currency": extract_currency,
}


def run_rule_layer(text: str, doc_type: DocType) -> dict[str, FieldResult]:
    extractors = INVOICE_EXTRACTORS if doc_type == DocType.INVOICE else CUSTOMS_EXTRACTORS
    return {name: fn(text) for name, fn in extractors.items()}
