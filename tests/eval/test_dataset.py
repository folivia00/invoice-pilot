"""Invariants of the committed eval dataset v1.0 (eval/dataset).

These tests read the generated files; they never render anything, so they run
in the normal test suite and in CI.
"""

import hashlib
import json
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from evalgen.pricing import TOTAL_EUR_MAX, TOTAL_EUR_MIN, eur_equivalent, price_bounds
from invoice_pilot.domain import Invoice, IssueCode, ValidationStatus, validate
from invoice_pilot.domain.currencies import minor_units

DATASET = Path(__file__).resolve().parents[2] / "eval" / "dataset"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_PAGES = 5


def _manifest() -> dict[str, Any]:
    return json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))


def _ground_truths() -> list[dict[str, Any]]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((DATASET / "ground_truth").glob("inv-*.json"))
    ]


def _catalogs() -> dict[str, dict[str, dict[str, Any]]]:
    """Catalog entries by language and printed description (from the locale files)."""
    catalogs: dict[str, dict[str, dict[str, Any]]] = {}
    for lang in ("en", "de"):
        locale = json.loads((DATASET / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
        catalogs[lang] = {entry["text"]: entry for entry in locale["items"]}
    return catalogs


MANIFEST = _manifest()
GROUND_TRUTHS = _ground_truths()
CATALOGS = _catalogs()
REFERENCE_DATE = date.fromisoformat(MANIFEST["reference_date"])
IDS = [gt["id"] for gt in GROUND_TRUTHS]


def _lines() -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """Every line item of the dataset as (ground truth, catalog entry, line item)."""
    lines = []
    for gt in GROUND_TRUTHS:
        items = gt["invoice"]["line_items"]
        assert len(gt["line_units"]) == len(items) >= 1
        for unit, item in zip(gt["line_units"], items, strict=True):
            entry = CATALOGS[gt["language"]][item["description"]]
            assert entry["unit"] == unit, (gt["id"], item)
            lines.append((gt, entry, item))
    return lines


@pytest.fixture(scope="module")
def by_id() -> dict[str, dict[str, Any]]:
    return {gt["id"]: gt for gt in GROUND_TRUTHS}


# --- composition --------------------------------------------------------------------


def test_manifest_header() -> None:
    assert MANIFEST["dataset_version"] == "1.0"
    assert MANIFEST["seed"] == 20261023
    assert MANIFEST["reference_date"] == "2026-10-01"
    assert MANIFEST["document_count"] == 120 == len(MANIFEST["documents"]) == len(GROUND_TRUTHS)
    assert set(MANIFEST["generator"]) == {"pillow", "reportlab", "font"}


def test_composition_counts() -> None:
    assert Counter(gt["input_type"] for gt in GROUND_TRUTHS) == {
        "text_pdf": 48,
        "scan_pdf": 36,
        "photo": 36,
    }
    assert Counter(gt["language"] for gt in GROUND_TRUTHS) == {"en": 72, "de": 48}
    assert sum(1 for gt in GROUND_TRUTHS if gt["corruption"]) == 15
    kinds = Counter(gt["corruption"] for gt in GROUND_TRUTHS if gt["corruption"])
    assert set(kinds) == {
        "total_mismatch",
        "line_amount_mismatch",
        "subtotal_mismatch",
        "missing_invoice_number",
        "bad_iban_check_digits",
        "due_before_invoice_date",
        "bad_vat_format",
    }
    assert all(count >= 2 for count in kinds.values())
    # corrupted documents appear in every input type and both languages
    assert {gt["input_type"] for gt in GROUND_TRUTHS if gt["corruption"]} == {
        "text_pdf",
        "scan_pdf",
        "photo",
    }
    assert {gt["language"] for gt in GROUND_TRUTHS if gt["corruption"]} == {"en", "de"}


def test_templates_currencies_pages() -> None:
    templates = Counter(gt["template"] for gt in GROUND_TRUTHS)
    assert len(templates) == 10 and all(n >= 10 for n in templates.values())
    currencies = Counter(gt["currency"] for gt in GROUND_TRUTHS)
    assert {"EUR", "USD", "GBP", "CHF", "JPY"} <= set(currencies)
    assert 3 <= currencies["JPY"] <= 5
    assert currencies.most_common(1)[0][0] == "EUR"
    two_pages = sum(1 for gt in GROUND_TRUTHS if gt["pages"] == 2)
    assert 6 <= two_pages <= 10
    assert all(1 <= gt["pages"] <= MAX_PAGES for gt in GROUND_TRUTHS)
    assert all(gt["input_type"] != "photo" or gt["pages"] == 1 for gt in GROUND_TRUTHS)


def test_formats_present() -> None:
    assert {gt["number_format"] for gt in GROUND_TRUTHS} == {"en", "de", "ch"}
    assert {gt["date_format"] for gt in GROUND_TRUTHS} == {
        "dmy_dot",
        "iso",
        "mdy_slash",
        "long_en",
        "long_de",
    }
    assert all(gt["currency"] == "USD" for gt in GROUND_TRUTHS if gt["date_format"] == "mdy_slash")


# --- ground truth invariants --------------------------------------------------------------


@pytest.mark.parametrize("doc_id", IDS)
def test_ground_truth_matches_validation(doc_id: str, by_id: dict[str, dict[str, Any]]) -> None:
    gt = by_id[doc_id]
    invoice = Invoice.model_validate(gt["invoice"])
    assert invoice.invoice_date is not None and invoice.invoice_date <= REFERENCE_DATE
    report = validate(invoice, today=REFERENCE_DATE)
    if gt["corruption"] is None:
        assert gt["expected_status"] == "auto_approved"
        assert gt["expected_issue_codes"] == []
        assert report.errors == [], [(i.code.value, i.field) for i in report.errors]
        assert report.status is ValidationStatus.AUTO_APPROVED
    else:
        assert gt["expected_status"] == "needs_review"
        assert report.status is ValidationStatus.NEEDS_REVIEW
        found = {i.code.value for i in report.issues}
        assert set(gt["expected_issue_codes"]) <= found, (gt["corruption"], found)
        assert all(IssueCode(code) for code in gt["expected_issue_codes"])


@pytest.mark.parametrize("doc_id", IDS)
def test_ground_truth_decimals_are_strings(doc_id: str, by_id: dict[str, dict[str, Any]]) -> None:
    invoice = by_id[doc_id]["invoice"]
    for key in ("subtotal", "tax_amount", "total", "tax_rate"):
        assert invoice[key] is None or isinstance(invoice[key], str)
    for item in invoice["line_items"]:
        for key in ("quantity", "unit_price", "amount"):
            assert isinstance(item[key], str)
    assert isinstance(invoice["invoice_date"], str) and len(invoice["invoice_date"]) == 10


# --- file integrity -----------------------------------------------------------------------


@pytest.mark.parametrize("doc_id", IDS)
def test_file_integrity(doc_id: str, by_id: dict[str, dict[str, Any]]) -> None:
    gt = by_id[doc_id]
    path = DATASET / gt["file"]
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    assert digest == gt["sha256"]
    entry = next(d for d in MANIFEST["documents"] if d["id"] == doc_id)
    assert entry["sha256"] == digest and entry["file"] == gt["file"]
    assert len(data) <= MAX_FILE_BYTES
    assert len(data) == gt["size_bytes"]
    if gt["input_type"] == "photo":
        assert path.suffix == ".jpg" and data[:3] == b"\xff\xd8\xff"
    else:
        assert path.suffix == ".pdf" and data[:5] == b"%PDF-"
        assert len(PdfReader(path).pages) == gt["pages"]


def test_no_stray_files() -> None:
    files = sorted(p.name for p in (DATASET / "docs").iterdir())
    expected = sorted(Path(d["file"]).name for d in MANIFEST["documents"])
    assert files == expected


# --- text layer -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "doc_id", [gt["id"] for gt in GROUND_TRUTHS if gt["input_type"] == "text_pdf"]
)
def test_text_pdf_has_printed_values_in_text_layer(
    doc_id: str, by_id: dict[str, dict[str, Any]]
) -> None:
    gt = by_id[doc_id]
    text = "".join(page.extract_text() or "" for page in PdfReader(DATASET / gt["file"]).pages)
    printed = gt["printed"]
    if printed["invoice_number"]:
        assert printed["invoice_number"] in text
    else:
        assert gt["corruption"] == "missing_invoice_number"
    assert printed["total"] in text
    if printed["iban"]:
        assert printed["iban"] in text


@pytest.mark.parametrize(
    "doc_id", [gt["id"] for gt in GROUND_TRUTHS if gt["input_type"] == "scan_pdf"]
)
def test_scan_pdf_has_no_text_layer(doc_id: str, by_id: dict[str, dict[str, Any]]) -> None:
    gt = by_id[doc_id]
    for page in PdfReader(DATASET / gt["file"]).pages:
        assert not (page.extract_text() or "").strip()


# --- quantities, prices and totals (tasks 06-fix1, 06-fix2) ---------------------------


def test_quantities_follow_unit_types() -> None:
    lines = _lines()
    fractional = sum(1 for _, _, item in lines if "." in item["quantity"].rstrip("0").rstrip("."))
    small_piece = small_hour = pieces = hours = 0
    for gt, entry, item in lines:
        quantity = Decimal(item["quantity"])
        is_whole = quantity == quantity.to_integral_value()
        unit = entry["unit"]
        assert unit in ("piece", "hour", "weight", "length", "flat")
        if unit in ("piece", "flat"):
            assert is_whole, (gt["id"], item)
        if unit == "flat":
            assert quantity == 1
        if unit == "piece":
            assert 1 <= quantity <= 50
            pieces += 1
            small_piece += quantity <= 10
        if unit == "hour":
            assert (quantity * 4) == (quantity * 4).to_integral_value()
            assert Decimal("0.5") <= quantity <= 80
            hours += 1
            small_hour += quantity <= 40
        if unit in ("weight", "length"):
            assert -quantity.as_tuple().exponent <= 2
            assert 1 <= quantity <= entry["qty_max"], (gt["id"], item)
    assert fractional / len(lines) >= 0.20, fractional / len(lines)
    # 80 % of piece quantities are 1-10 and 80 % of hour quantities are 0.5-40 (with slack)
    assert small_piece / pieces >= 0.7 and small_hour / hours >= 0.7


def test_unit_prices_follow_catalog_ranges() -> None:
    lines = _lines()
    round_prices = 0
    for gt, entry, item in lines:
        units = minor_units(gt["currency"])
        assert units is not None
        low, high = price_bounds(entry, gt["currency"], units)
        price = Decimal(item["unit_price"])
        assert low <= price <= high, (gt["id"], item, low, high)
        assert -price.as_tuple().exponent == units, (gt["id"], item)
        round_prices += price == price.to_integral_value()
    # about 30 % of the prices are whole currency units (JPY prices always are)
    assert 0.2 <= round_prices / len(lines) <= 0.45, round_prices / len(lines)


def test_totals_within_eur_range() -> None:
    for gt in GROUND_TRUTHS:
        total_eur = eur_equivalent(Decimal(gt["invoice"]["total"]), gt["currency"])
        assert TOTAL_EUR_MIN <= total_eur <= TOTAL_EUR_MAX, (gt["id"], total_eur)
