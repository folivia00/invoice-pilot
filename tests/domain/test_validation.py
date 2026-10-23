"""Unit tests for every validation rule: one passing and one failing case each."""

from datetime import date
from decimal import Decimal

import pytest

from invoice_pilot.domain import (
    Invoice,
    IssueCode,
    LineItem,
    Party,
    Severity,
    ValidationStatus,
    validate,
)
from invoice_pilot.domain.iban import compute_check_digits
from invoice_pilot.domain.validation import REQUIRED_FIELDS, REQUIRED_LINE_ITEM_FIELDS

TODAY = date(2026, 10, 23)
D = Decimal


def iban(country: str, bban: str) -> str:
    return country + compute_check_digits(country, bban) + bban


def eur_invoice(**overrides: object) -> Invoice:
    base = Invoice(
        supplier=Party(name="Supplier GmbH", tax_id="DE123456789", address="Berlin"),
        buyer=Party(name="Buyer AG", tax_id="ATU12345678", address="Vienna"),
        invoice_number="INV-2026-001",
        invoice_date=date(2026, 10, 1),
        due_date=date(2026, 10, 31),
        currency="EUR",
        line_items=[
            LineItem(
                description="Widget", quantity=D("2"), unit_price=D("10.00"), amount=D("20.00")
            ),
            LineItem(
                description="Cable", quantity=D("1.5"), unit_price=D("3.30"), amount=D("4.95")
            ),
        ],
        subtotal=D("24.95"),
        tax_rate=D("19"),
        tax_amount=D("4.74"),  # 24.95 * 0.19 = 4.7405
        total=D("29.69"),
        iban=iban("DE", "370400440532013000"),
        bic="COBADEFFXXX",
    )
    return base.model_copy(update=overrides)


def jpy_invoice(**overrides: object) -> Invoice:
    base = eur_invoice(
        currency="JPY",
        line_items=[
            LineItem(description="Service", quantity=D("3"), unit_price=D("100"), amount=D("300"))
        ],
        subtotal=D("300"),
        tax_rate=D("10"),
        tax_amount=D("30"),
        total=D("330"),
    )
    return base.model_copy(update=overrides)


def codes(invoice: Invoice) -> set[IssueCode]:
    return validate(invoice, today=TODAY).codes


# --- baseline -----------------------------------------------------------------


def test_consistent_invoice_is_auto_approved() -> None:
    report = validate(eur_invoice(), today=TODAY)
    assert report.status is ValidationStatus.AUTO_APPROVED
    assert report.errors == []
    # Only the IBAN length warning (registry table pending) is allowed.
    assert {w.code for w in report.warnings} <= {IssueCode.IBAN_LENGTH_UNVERIFIED}


def test_status_is_needs_review_only_for_errors() -> None:
    # A warning alone (unknown tax ID prefix) keeps the invoice auto-approved.
    report = validate(eur_invoice(supplier=Party(name="S", tax_id="US999")), today=TODAY)
    assert report.status is ValidationStatus.AUTO_APPROVED
    assert report.has(IssueCode.TAX_ID_UNVERIFIED, "supplier.tax_id")
    assert all(i.severity is Severity.WARNING for i in report.issues)


# --- rule a: required fields ----------------------------------------------------


@pytest.mark.parametrize("path", REQUIRED_FIELDS)
def test_missing_required_field_is_error_with_path(path: str) -> None:
    invoice = eur_invoice()
    if path == "supplier.name":
        invoice = invoice.model_copy(update={"supplier": Party(name=None)})
    elif path == "buyer.name":
        invoice = invoice.model_copy(update={"buyer": Party(name=None)})
    elif path == "line_items":
        invoice = invoice.model_copy(update={"line_items": []})
    else:
        invoice = invoice.model_copy(update={path: None})
    report = validate(invoice, today=TODAY)
    assert report.status is ValidationStatus.NEEDS_REVIEW
    assert report.has(IssueCode.MISSING_FIELD, path)


@pytest.mark.parametrize("name", REQUIRED_LINE_ITEM_FIELDS)
def test_missing_line_item_field_has_indexed_path(name: str) -> None:
    invoice = eur_invoice()
    items = list(invoice.line_items)
    items[1] = items[1].model_copy(update={name: None})
    report = validate(invoice.model_copy(update={"line_items": items}), today=TODAY)
    assert report.has(IssueCode.MISSING_FIELD, f"line_items[1].{name}")


def test_blank_string_counts_as_missing() -> None:
    assert IssueCode.MISSING_FIELD in codes(eur_invoice(invoice_number="   "))


# --- rule b: currency -------------------------------------------------------------


def test_valid_currency_is_case_insensitive() -> None:
    assert IssueCode.INVALID_CURRENCY not in codes(eur_invoice(currency="eur"))


@pytest.mark.parametrize("currency", ["XAU", "CLF", "EU", "EURO"])
def test_invalid_currency_is_error(currency: str) -> None:
    report = validate(eur_invoice(currency=currency), today=TODAY)
    assert report.has(IssueCode.INVALID_CURRENCY, "currency")
    assert report.status is ValidationStatus.NEEDS_REVIEW


def test_invalid_currency_does_not_hide_sum_errors() -> None:
    # With an unsupported currency the sums are still checked (exact match).
    report = validate(eur_invoice(currency="XAU", total=D("29.74")), today=TODAY)
    assert report.has(IssueCode.INVALID_CURRENCY) and report.has(IssueCode.TOTAL_MISMATCH)


# --- rule c: exact sums, rounding tolerance by currency ---------------------------


def test_eur_total_must_match_exactly() -> None:
    assert IssueCode.TOTAL_MISMATCH not in codes(eur_invoice(total=D("29.69")))
    assert IssueCode.TOTAL_MISMATCH in codes(eur_invoice(total=D("29.70")))


def test_jpy_total_must_match_exactly() -> None:
    assert validate(jpy_invoice(), today=TODAY).status is ValidationStatus.AUTO_APPROVED
    assert IssueCode.TOTAL_MISMATCH in codes(jpy_invoice(total=D("331")))


# --- rule d: line arithmetic -----------------------------------------------------


def test_line_amount_within_tolerance_passes() -> None:
    items = [LineItem(description="x", quantity=D("3"), unit_price=D("0.333"), amount=D("1.00"))]
    invoice = eur_invoice(
        line_items=items, subtotal=D("1.00"), tax_rate=None, tax_amount=D("0.19"), total=D("1.19")
    )
    assert IssueCode.LINE_AMOUNT_MISMATCH not in codes(invoice)


def test_line_amount_mismatch_is_error_with_index() -> None:
    items = list(eur_invoice().line_items)
    items[1] = items[1].model_copy(update={"amount": D("5.95")})
    report = validate(eur_invoice(line_items=items), today=TODAY)
    assert report.has(IssueCode.LINE_AMOUNT_MISMATCH, "line_items[1].amount")
    assert report.status is ValidationStatus.NEEDS_REVIEW


# --- rule e: subtotal -----------------------------------------------------------


def test_subtotal_mismatch() -> None:
    report = validate(eur_invoice(subtotal=D("25.95")), today=TODAY)
    assert report.has(IssueCode.SUBTOTAL_MISMATCH, "subtotal")


def test_subtotal_not_checked_when_an_amount_is_missing() -> None:
    items = list(eur_invoice().line_items)
    items[0] = items[0].model_copy(update={"amount": None})
    found = codes(eur_invoice(line_items=items))
    assert IssueCode.MISSING_FIELD in found and IssueCode.SUBTOTAL_MISMATCH not in found


# --- rule f: total --------------------------------------------------------------


def test_total_mismatch() -> None:
    report = validate(eur_invoice(total=D("30.69")), today=TODAY)
    assert report.has(IssueCode.TOTAL_MISMATCH, "total")


# --- rule g: tax rate -----------------------------------------------------------


def test_tax_rate_consistent_passes_and_optional() -> None:
    assert IssueCode.TAX_AMOUNT_MISMATCH not in codes(eur_invoice())
    assert IssueCode.TAX_AMOUNT_MISMATCH not in codes(eur_invoice(tax_rate=None))


def test_tax_amount_mismatch() -> None:
    report = validate(eur_invoice(tax_rate=D("7")), today=TODAY)
    assert report.has(IssueCode.TAX_AMOUNT_MISMATCH, "tax_amount")


@pytest.mark.parametrize("rate", [D("-1"), D("100.01"), D("119")])
def test_tax_rate_out_of_range(rate: Decimal) -> None:
    report = validate(eur_invoice(tax_rate=rate), today=TODAY)
    assert report.has(IssueCode.TAX_RATE_OUT_OF_RANGE, "tax_rate")
    assert not report.has(IssueCode.TAX_AMOUNT_MISMATCH)


def test_zero_tax_rate_is_valid() -> None:
    invoice = eur_invoice(tax_rate=D("0"), tax_amount=D("0.00"), total=D("24.95"))
    assert validate(invoice, today=TODAY).status is ValidationStatus.AUTO_APPROVED


# --- rule h: negative values ----------------------------------------------------


def test_negative_total_is_error() -> None:
    report = validate(eur_invoice(total=D("-29.69")), today=TODAY)
    assert report.has(IssueCode.NEGATIVE_VALUE, "total")


def test_negative_quantity_is_error_with_path() -> None:
    items = [LineItem(description="x", quantity=D("-1"), unit_price=D("2"), amount=D("-2"))]
    report = validate(eur_invoice(line_items=items), today=TODAY)
    assert report.has(IssueCode.NEGATIVE_VALUE, "line_items[0].quantity")
    assert report.has(IssueCode.NEGATIVE_VALUE, "line_items[0].amount")


# --- rule i: dates ----------------------------------------------------------------


def test_due_date_equal_to_invoice_date_is_fine() -> None:
    assert IssueCode.DUE_BEFORE_INVOICE_DATE not in codes(eur_invoice(due_date=date(2026, 10, 1)))


def test_due_date_before_invoice_date_is_error() -> None:
    report = validate(eur_invoice(due_date=date(2026, 9, 30)), today=TODAY)
    assert report.has(IssueCode.DUE_BEFORE_INVOICE_DATE, "due_date")


def test_invoice_date_today_is_fine_but_tomorrow_is_error() -> None:
    assert IssueCode.INVOICE_DATE_IN_FUTURE not in codes(
        eur_invoice(invoice_date=TODAY, due_date=TODAY)
    )
    report = validate(eur_invoice(invoice_date=date(2026, 10, 24)), today=TODAY)
    assert report.has(IssueCode.INVOICE_DATE_IN_FUTURE, "invoice_date")


# --- rule j: IBAN -----------------------------------------------------------------


def test_iban_with_spaces_and_lowercase_passes_checksum() -> None:
    value = iban("DE", "370400440532013000")
    spaced = " ".join(value[i : i + 4] for i in range(0, len(value), 4)).lower()
    report = validate(eur_invoice(iban=spaced), today=TODAY)
    assert not report.has(IssueCode.INVALID_IBAN)


def test_iban_bad_checksum_is_error() -> None:
    value = iban("DE", "370400440532013000")
    broken = value[:-1] + ("0" if value[-1] != "0" else "1")
    report = validate(eur_invoice(iban=broken), today=TODAY)
    assert report.has(IssueCode.INVALID_IBAN, "iban")
    assert report.status is ValidationStatus.NEEDS_REVIEW


def test_iban_unknown_country_length_is_warning_only() -> None:
    report = validate(eur_invoice(iban=iban("ZZ", "1234567890")), today=TODAY)
    assert report.has(IssueCode.IBAN_LENGTH_UNVERIFIED, "iban")
    assert report.status is ValidationStatus.AUTO_APPROVED


def test_missing_iban_is_not_an_issue() -> None:
    found = codes(eur_invoice(iban=None))
    assert IssueCode.INVALID_IBAN not in found and IssueCode.IBAN_LENGTH_UNVERIFIED not in found


# --- rule k: tax IDs --------------------------------------------------------------


def test_valid_tax_ids_pass() -> None:
    found = codes(eur_invoice())
    assert IssueCode.INVALID_TAX_ID not in found and IssueCode.TAX_ID_UNVERIFIED not in found


def test_invalid_tax_id_format_is_error() -> None:
    report = validate(eur_invoice(buyer=Party(name="B", tax_id="AT12345678")), today=TODAY)
    assert report.has(IssueCode.INVALID_TAX_ID, "buyer.tax_id")
    assert report.status is ValidationStatus.NEEDS_REVIEW


def test_ein_is_accepted() -> None:
    report = validate(eur_invoice(buyer=Party(name="B", tax_id="12-3456789")), today=TODAY)
    assert not report.has(IssueCode.INVALID_TAX_ID) and not report.has(IssueCode.TAX_ID_UNVERIFIED)


# --- rule l: zero total -----------------------------------------------------------


def test_zero_total_with_line_items_is_error() -> None:
    items = [LineItem(description="x", quantity=D("1"), unit_price=D("0"), amount=D("0"))]
    invoice = eur_invoice(
        line_items=items, subtotal=D("0"), tax_rate=D("19"), tax_amount=D("0"), total=D("0")
    )
    report = validate(invoice, today=TODAY)
    assert report.has(IssueCode.ZERO_TOTAL, "total")


def test_zero_total_without_line_items_only_reports_missing() -> None:
    invoice = eur_invoice(line_items=[], subtotal=D("0"), tax_amount=D("0"), total=D("0"))
    found = codes(invoice)
    assert IssueCode.ZERO_TOTAL not in found and IssueCode.MISSING_FIELD in found


# --- report shape -------------------------------------------------------------------


def test_issue_messages_are_human_readable_english() -> None:
    report = validate(eur_invoice(total=D("30.69"), currency="XAU"), today=TODAY)
    for issue in report.issues:
        assert issue.message.endswith(".")
        assert issue.message.isascii()
    assert report.has(IssueCode.TOTAL_MISMATCH) and report.has(IssueCode.INVALID_CURRENCY)


# --- tolerance semantics (task 05-fix1) ----------------------------------------------
# Rules e and f compare sums of already rounded amounts: exact Decimal equality.
# Rules d and g compare a computed value with a rounded one: half a minor unit.


def test_total_off_by_one_cent_is_error() -> None:
    invoice = eur_invoice(
        line_items=[
            LineItem(
                description="Service",
                quantity=D("1"),
                unit_price=D("1000.00"),
                amount=D("1000.00"),
            )
        ],
        subtotal=D("1000.00"),
        tax_rate=D("19"),
        tax_amount=D("190.00"),
        total=D("1190.01"),
    )
    report = validate(invoice, today=TODAY)
    assert report.status is ValidationStatus.NEEDS_REVIEW
    assert report.has(IssueCode.TOTAL_MISMATCH, "total")


def test_subtotal_off_by_one_cent_is_error() -> None:
    items = [
        LineItem(description="A", quantity=D("1"), unit_price=D("100.00"), amount=D("100.00")),
        LineItem(description="B", quantity=D("1"), unit_price=D("200.00"), amount=D("200.00")),
    ]
    invoice = eur_invoice(
        line_items=items,
        subtotal=D("300.01"),
        tax_rate=None,
        tax_amount=D("57.00"),
        total=D("357.01"),
    )
    report = validate(invoice, today=TODAY)
    assert report.status is ValidationStatus.NEEDS_REVIEW
    assert report.has(IssueCode.SUBTOTAL_MISMATCH, "subtotal")


def test_jpy_total_off_by_one_yen_is_error() -> None:
    invoice = jpy_invoice(
        line_items=[
            LineItem(description="Service", quantity=D("1"), unit_price=D("1000"), amount=D("1000"))
        ],
        subtotal=D("1000"),
        tax_rate=D("10"),
        tax_amount=D("100"),
        total=D("1101"),
    )
    report = validate(invoice, today=TODAY)
    assert report.status is ValidationStatus.NEEDS_REVIEW
    assert report.has(IssueCode.TOTAL_MISMATCH, "total")


def test_line_rounding_within_half_cent_passes() -> None:
    # 3 x 0.333 = 0.999, amount 1.00: |0.999 - 1.00| = 0.001 <= 0.005
    items = [LineItem(description="x", quantity=D("3"), unit_price=D("0.333"), amount=D("1.00"))]
    invoice = eur_invoice(
        line_items=items, subtotal=D("1.00"), tax_rate=None, tax_amount=D("0.19"), total=D("1.19")
    )
    report = validate(invoice, today=TODAY)
    assert report.status is ValidationStatus.AUTO_APPROVED
    assert not report.has(IssueCode.LINE_AMOUNT_MISMATCH)


def test_line_amount_off_by_one_cent_is_error() -> None:
    # 1 x 10.00 = 10.00, amount 10.01: |10.00 - 10.01| = 0.01 > 0.005
    items = [LineItem(description="x", quantity=D("1"), unit_price=D("10.00"), amount=D("10.01"))]
    invoice = eur_invoice(
        line_items=items, subtotal=D("10.01"), tax_rate=None, tax_amount=D("1.90"), total=D("11.91")
    )
    report = validate(invoice, today=TODAY)
    assert report.status is ValidationStatus.NEEDS_REVIEW
    assert report.has(IssueCode.LINE_AMOUNT_MISMATCH, "line_items[0].amount")


def test_tax_rounding_within_half_unit_passes_but_more_fails() -> None:
    # JPY: 303 x 10 % = 30.3 -> tax 30 passes (0.3 <= 0.5), tax 31 fails (0.7 > 0.5).
    base = jpy_invoice(
        line_items=[
            LineItem(description="S", quantity=D("1"), unit_price=D("303"), amount=D("303"))
        ],
        subtotal=D("303"),
        tax_rate=D("10"),
        tax_amount=D("30"),
        total=D("333"),
    )
    assert validate(base, today=TODAY).status is ValidationStatus.AUTO_APPROVED
    off = base.model_copy(update={"tax_amount": D("31"), "total": D("334")})
    report = validate(off, today=TODAY)
    assert report.has(IssueCode.TAX_AMOUNT_MISMATCH, "tax_amount")
    assert not report.has(IssueCode.TOTAL_MISMATCH)
