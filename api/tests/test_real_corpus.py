"""The real-corpus path: adapters, comparators, and the manifest between them.

None of this touches the network. What is worth pinning is the scoring, since
a comparator that drifts loose would raise the reported accuracy on real
documents without anything getting better.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "eval"))

from adapters import common  # imports need the sys.path lines above
from compare import compare

AMOUNTS = ["1.234,56", "1,234.56", "1234.56", "9.00", "60.000", "RM 12.00", "$ 7", "abc", "-5"]


@pytest.mark.parametrize("raw", AMOUNTS)
def test_adapter_amount_normalizer_matches_the_engine(raw):
    """The adapter normalizes ground truth, the engine normalizes what it read.

    They are separate copies — the adapters run outside the api/ package — so
    they are compared here rather than trusted to stay identical.
    """
    from engine.rules import normalize_amount as engine_normalize

    assert common.normalize_amount(raw) == engine_normalize(raw)


@pytest.mark.parametrize(
    "raw, iso",
    [("25/12/2018", "2018-12-25"), ("12-01-19", "2019-01-12"), ("23-01-2019", "2019-01-23")],
)
def test_dates_are_normalized_day_first(raw, iso):
    """SROIE is Malaysian: 12-01-19 is 12 January, not 1 December."""
    assert common.normalize_date(raw) == iso


def test_unparseable_date_is_dropped_not_guessed():
    assert common.normalize_date("n/a") is None


def test_name_comparator_accepts_annotation_noise_but_not_a_different_merchant():
    assert compare("name", "BOOK TA.K (TAMAN DAYA) SDN BHD", "BOOK TA .K (TAMAN DAYA) SDN BHD").ok
    assert compare("name", "MR D.I.Y. (JOHOR) SDN BHD", "INDAH GIFT & HOME DECO").ok is False


def test_amount_comparator_reports_a_thousands_locale_mismatch_as_such():
    """60.000 IDR read as 60000 is a separator convention, not a misread digit.

    It is still scored wrong — the value does not match — but it is labelled,
    because a run full of these means the locale needs handling, not the model.
    """
    verdict = compare("amount", "60000", "60.00")
    assert not verdict.ok
    assert verdict.note == "scale x1000"
    assert compare("amount", "60.00", "60.0").ok


def test_missing_on_both_sides_is_not_a_miss():
    """A field the document genuinely does not have, and that nothing invented."""
    assert compare("exact", None, None).ok
    assert compare("exact", None, "EUR").ok is False


def test_manifest_rejects_a_comparator_the_evaluator_cannot_run(tmp_path):
    with pytest.raises(ValueError, match="unknown comparator"):
        common.write_manifest(
            tmp_path,
            dataset="x",
            source="s",
            licence="l",
            scored_fields={"total_amount": "vibes"},
            documents={},
        )


def test_manifest_round_trips_into_the_evaluator(tmp_path):
    from evaluate import load_manifest

    path = common.write_manifest(
        tmp_path,
        dataset="sroie",
        source="https://example.invalid",
        licence="research use",
        scored_fields={"total_amount": "amount"},
        documents={"sroie_000.jpg": {"doc_type": None, "fields": {"total_amount": "9.00"}}},
    )
    assert json.loads(path.read_text())["manifest_version"] == common.MANIFEST_VERSION

    corpus, scored, label, _ = load_manifest(path)
    assert scored == {"total_amount": "amount"}
    assert corpus[0][0] == tmp_path / "sroie_000.jpg"
    assert corpus[0][2] is None, "a receipt is not one of this project's doc types"
    assert "sroie" in label


def _annotation(fields, currency="usd", doc_type="tax_invoice"):
    return {
        "metadata": {"currency": currency, "document_type": doc_type},
        "field_extractions": [
            {"fieldtype": ft, "text": text, "page": page, "bbox": [0.1, top, 0.2, top + 0.01]}
            for ft, text, page, top in fields
        ],
    }


def test_docile_fields_map_onto_this_projects_schema():
    from adapters.docile import _expected_fields

    got = _expected_fields(_annotation([
        ("document_id", "PR12-018795", 0, 0.3),
        ("date_issue", "06/07/99", 0, 0.32),
        ("vendor_name", "BOZELL WORLDWIDE, INC.", 0, 0.1),
        ("amount_total_gross", "$1,726.73", 0, 0.8),
    ]))
    assert got == {
        "invoice_number": "PR12-018795",
        "invoice_date": "1999-06-07",   # month-first: DocILE is a US corpus
        "supplier_name": "BOZELL WORLDWIDE, INC.",
        "total_amount": "1726.73",
        "currency": "USD",
    }


def test_docile_total_falls_back_to_amount_due():
    from adapters.docile import _expected_fields

    got = _expected_fields(_annotation([("amount_due", "400.00", 0, 0.9)]))
    assert got["total_amount"] == "400.00"


def test_docile_takes_the_topmost_occurrence_of_a_repeated_field():
    """A field type recurs across pages and columns; the header one is the document's."""
    from adapters.docile import _expected_fields

    got = _expected_fields(_annotation([
        ("vendor_name", "second page copy", 1, 0.1),
        ("vendor_name", "MANAGEMENT SCIENCE ASSOCIATES, INC.", 0, 0.05),
        ("vendor_name", "footer repeat", 0, 0.95),
    ]))
    assert got["supplier_name"] == "MANAGEMENT SCIENCE ASSOCIATES, INC."


def test_docile_leaves_out_what_it_cannot_normalize_rather_than_guessing():
    """An unparseable date is not scored for that document; a currency DocILE
    itself records as 'other' is not turned into an ISO code."""
    from adapters.docile import _expected_fields

    got = _expected_fields(
        _annotation(
            [("date_issue", "n/a", 0, 0.3), ("document_id", "12256", 0, 0.2)],
            currency="other",
        )
    )
    assert "invoice_date" not in got
    assert "currency" not in got
    assert got["invoice_number"] == "12256"


def test_docile_prepare_explains_how_to_get_the_data(tmp_path):
    from adapters.docile import prepare

    with pytest.raises(SystemExit, match="download_dataset.sh"):
        prepare(tmp_path / "out", 5, root=tmp_path / "missing")


@pytest.mark.parametrize(
    "raw, rupiah",
    [("60.000", "60000.00"), ("91,000", "91000.00"), ("281.435", "281435.00"), ("", None)],
)
def test_cord_prices_are_read_as_whole_rupiah(raw, rupiah):
    """CORD writes the thousands separator both ways inside one corpus, and the
    generic normalizer read the rightmost one as a decimal point — scoring the
    model's correct 60000 against a ground truth of 60.00."""
    from adapters.cord import _rupiah

    assert _rupiah(raw) == rupiah
