from engine import rules
from models.schemas import DocType, ExtractionMethod

# ---------------------------------------------------------------- amounts

def test_normalize_amount_european():
    assert rules.normalize_amount("1.234,56") == "1234.56"

def test_normalize_amount_us():
    assert rules.normalize_amount("1,234.56") == "1234.56"

def test_normalize_amount_plain():
    assert rules.normalize_amount("999") == "999.00"

def test_normalize_amount_garbage():
    assert rules.normalize_amount("abc") is None

def test_normalize_amount_negative_rejected():
    assert rules.normalize_amount("-50,00") is None


# ---------------------------------------------------------------- IBAN

VALID_IBAN = "DE89370400440532013000"  # official example IBAN, checksum-valid

def test_iban_valid_checksum():
    r = rules.extract_iban(f"Bank details IBAN: {VALID_IBAN}")
    assert r.value == VALID_IBAN
    assert r.confidence == 1.0
    assert r.method == ExtractionMethod.RULE

def test_iban_invalid_checksum_rejected():
    bad = "DE89370400440532013001"
    r = rules.extract_iban(f"IBAN: {bad}")
    assert r.value is None

def test_iban_with_spaces():
    r = rules.extract_iban("IBAN: DE89 3704 0044 0532 0130 00")
    assert r.value == VALID_IBAN


# ---------------------------------------------------------------- dates

def test_date_german_format():
    r = rules.extract_invoice_date("Rechnungsdatum: 03.02.2025")
    assert r.value == "2025-02-03"

def test_date_iso_format():
    r = rules.extract_invoice_date("Invoice date: 2025-06-30")
    assert r.value == "2025-06-30"

def test_date_in_future_rejected():
    r = rules.extract_invoice_date("Invoice date: 01.01.2099")
    assert r.value is None

def test_parse_date_before_2000_rejected():
    assert rules.parse_date("01.01.1999") is None


# ---------------------------------------------------------------- invoice number / VAT

def test_invoice_number_labeled():
    r = rules.extract_invoice_number("Invoice No: INV-2025-00123")
    assert r.value == "INV-2025-00123"

def test_invoice_number_german_label():
    r = rules.extract_invoice_number("Rechnungsnummer: RG/2025/778")
    assert r.value == "RG/2025/778"

def test_invoice_number_absent():
    assert rules.extract_invoice_number("no identifiers here").value is None

def test_vat_id_german():
    r = rules.extract_vat_id("USt-ID: DE123456789")
    assert r.value == "DE123456789"

def test_vat_id_dutch():
    r = rules.extract_vat_id("VAT: NL123456789B01")
    assert r.value == "NL123456789B01"

def test_vat_id_invalid_length():
    assert rules.extract_vat_id("DE12345").value is None


# ---------------------------------------------------------------- currency / totals

def test_currency_code():
    assert rules.extract_currency("Total: 100 EUR").value == "EUR"

def test_currency_euro_symbol():
    r = rules.extract_currency("Betrag: € 250,00")
    assert r.value == "EUR"

def test_total_amount_european():
    r = rules.extract_total_amount("Gesamtbetrag: EUR 1.234,56")
    assert r.value == "1234.56"

def test_total_amount_english():
    r = rules.extract_total_amount("Total due: $ 2,500.00")
    assert r.value == "2500.00"


# ---------------------------------------------------------------- customs fields

def test_hs_code_valid():
    r = rules.extract_hs_code("HS Code: 851713")
    assert r.value == "851713"

def test_hs_code_invalid_chapter():
    assert rules.extract_hs_code("HS Code: 990000").value is None

def test_validate_hs_code_length():
    assert not rules.validate_hs_code("12345")       # too short
    assert not rules.validate_hs_code("12345678901") # too long

def test_country_of_origin():
    r = rules.extract_country_of_origin("Country of origin: DE")
    assert r.value == "DE"

def test_country_of_origin_invalid():
    assert rules.extract_country_of_origin("Country of origin: XX").value is None

def test_gross_weight():
    r = rules.extract_gross_weight("Gross weight (kg): 12,5 kg")
    assert r.value == "12.50"

def test_declaration_number():
    r = rules.extract_declaration_number("Declaration No: 25DE1234567890AB")
    assert r.value == "25DE1234567890AB"


# ---------------------------------------------------------------- doc type

def test_classify_invoice():
    t, c = rules.classify_doc_type("INVOICE\nInvoice number: INV-1\nIBAN: xx\nVAT: yy")
    assert t == DocType.INVOICE
    assert c >= 0.6

def test_classify_customs():
    t, c = rules.classify_doc_type("CUSTOMS DECLARATION CN23\nHS Code: 640411\nCountry of origin: CN")
    assert t == DocType.CUSTOMS_FORM
    assert c >= 0.6

def test_classify_unknown():
    t, c = rules.classify_doc_type("completely unrelated text about weather")
    assert t == DocType.UNKNOWN
    assert c == 0.0
