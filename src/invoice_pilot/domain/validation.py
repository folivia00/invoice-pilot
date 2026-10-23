"""Deterministic validation rules for an extracted invoice.

``validate`` is a pure function: no I/O, no clock (``today`` is injected), no
logging. It never mutates the invoice; identifiers are normalized on the fly
with the helpers in ``normalize.py``.
"""

from datetime import date
from decimal import Decimal
from typing import Final

from invoice_pilot.domain.constants import (
    EXACT_MATCH_TOLERANCE,
    FALLBACK_MINOR_UNITS,
    HUNDRED,
    MAX_TAX_RATE,
    MIN_TAX_RATE,
    ZERO,
    rounding_tolerance_for,
)
from invoice_pilot.domain.currencies import minor_units
from invoice_pilot.domain.iban import check_iban
from invoice_pilot.domain.issues import (
    Issue,
    IssueCode,
    Severity,
    ValidationReport,
    ValidationStatus,
)
from invoice_pilot.domain.models import Invoice
from invoice_pilot.domain.normalize import normalize_currency, normalize_iban, normalize_tax_id
from invoice_pilot.domain.tax_ids import TaxIdStatus, check_tax_id

#: Fields that must be present (and non-empty) for an invoice to be auto-approved.
REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "supplier.name",
    "buyer.name",
    "invoice_number",
    "invoice_date",
    "currency",
    "subtotal",
    "tax_amount",
    "total",
    "line_items",
)

#: Fields that must be present on every line item.
REQUIRED_LINE_ITEM_FIELDS: Final[tuple[str, ...]] = (
    "description",
    "quantity",
    "unit_price",
    "amount",
)

_PARTIES: Final[tuple[str, ...]] = ("supplier", "buyer")


def validate(invoice: Invoice, *, today: date) -> ValidationReport:
    """Run all rules and decide between auto_approved and needs_review."""
    issues: list[Issue] = []
    _check_required(invoice, issues)
    units = _check_currency(invoice, issues)
    rounding_tolerance = rounding_tolerance_for(units)
    _check_negative_values(invoice, issues)
    _check_line_items(invoice, rounding_tolerance, issues)
    _check_sums(invoice, rounding_tolerance, issues)
    _check_dates(invoice, today, issues)
    _check_iban(invoice, issues)
    _check_tax_ids(invoice, issues)
    has_error = any(issue.severity is Severity.ERROR for issue in issues)
    status = ValidationStatus.NEEDS_REVIEW if has_error else ValidationStatus.AUTO_APPROVED
    return ValidationReport(status=status, issues=issues)


# --- helpers ------------------------------------------------------------------


def _error(code: IssueCode, field: str, message: str) -> Issue:
    return Issue(code=code, field=field, severity=Severity.ERROR, message=message)


def _warning(code: IssueCode, field: str, message: str) -> Issue:
    return Issue(code=code, field=field, severity=Severity.WARNING, message=message)


def _resolve(invoice: Invoice, path: str) -> object:
    value: object = invoice
    for part in path.split("."):
        value = getattr(value, part)
    return value


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value
    return False


def _within(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    return abs(a - b) <= tolerance


# --- rules --------------------------------------------------------------------


def _check_required(invoice: Invoice, issues: list[Issue]) -> None:
    """Rule a: required fields for auto-approval."""
    for path in REQUIRED_FIELDS:
        if _is_missing(_resolve(invoice, path)):
            issues.append(
                _error(IssueCode.MISSING_FIELD, path, f"Required field '{path}' is missing.")
            )
    for index, item in enumerate(invoice.line_items):
        for name in REQUIRED_LINE_ITEM_FIELDS:
            if _is_missing(getattr(item, name)):
                path = f"line_items[{index}].{name}"
                issues.append(
                    _error(IssueCode.MISSING_FIELD, path, f"Required field '{path}' is missing.")
                )


def _check_currency(invoice: Invoice, issues: list[Issue]) -> int:
    """Rule b: active ISO 4217 code with minor units. Returns minor units for rule c.

    Rule c: the minor units drive the rounding tolerance of rules d and g
    (half a unit); rules e and f are exact. An unsupported currency falls back
    to two decimals without hiding the currency error.
    """
    if invoice.currency is None or not invoice.currency.strip():
        return FALLBACK_MINOR_UNITS
    units = minor_units(normalize_currency(invoice.currency))
    if units is None:
        issues.append(
            _error(
                IssueCode.INVALID_CURRENCY,
                "currency",
                f"Currency '{invoice.currency}' is not an active ISO 4217 currency "
                "with minor units.",
            )
        )
        return FALLBACK_MINOR_UNITS
    return units


def _check_negative_values(invoice: Invoice, issues: list[Issue]) -> None:
    """Rule h: no negative amounts or quantities (credit notes are out of scope)."""
    fields: list[tuple[str, Decimal | None]] = [
        ("subtotal", invoice.subtotal),
        ("tax_amount", invoice.tax_amount),
        ("total", invoice.total),
    ]
    for index, item in enumerate(invoice.line_items):
        fields.append((f"line_items[{index}].quantity", item.quantity))
        fields.append((f"line_items[{index}].unit_price", item.unit_price))
        fields.append((f"line_items[{index}].amount", item.amount))
    for path, value in fields:
        if value is not None and value < ZERO:
            issues.append(
                _error(IssueCode.NEGATIVE_VALUE, path, f"'{path}' is negative ({value}).")
            )


def _check_line_items(invoice: Invoice, tolerance: Decimal, issues: list[Issue]) -> None:
    """Rule d: quantity x unit_price = amount within the rounding tolerance (half a unit)."""
    for index, item in enumerate(invoice.line_items):
        if item.quantity is None or item.unit_price is None or item.amount is None:
            continue
        expected = item.quantity * item.unit_price
        if not _within(expected, item.amount, tolerance):
            path = f"line_items[{index}].amount"
            issues.append(
                _error(
                    IssueCode.LINE_AMOUNT_MISMATCH,
                    path,
                    f"Line amount {item.amount} differs from quantity x unit price "
                    f"({item.quantity} x {item.unit_price} = {expected}) by more than {tolerance}.",
                )
            )


def _check_sums(invoice: Invoice, tolerance: Decimal, issues: list[Issue]) -> None:
    """Rules e, f, g, l: subtotal, total, tax and zero-total consistency.

    e and f compare already rounded money values and require an exact match;
    g compares a computed tax with a rounded one and uses ``tolerance``.
    """
    subtotal, tax_amount, total = invoice.subtotal, invoice.tax_amount, invoice.total
    amounts = [item.amount for item in invoice.line_items]
    if subtotal is not None and amounts and all(a is not None for a in amounts):
        items_sum = sum((a for a in amounts if a is not None), ZERO)
        if not _within(items_sum, subtotal, EXACT_MATCH_TOLERANCE):
            issues.append(
                _error(
                    IssueCode.SUBTOTAL_MISMATCH,
                    "subtotal",
                    f"Subtotal {subtotal} does not equal the sum of line amounts {items_sum} "
                    "(exact match required).",
                )
            )
    if subtotal is not None and tax_amount is not None and total is not None:
        if not _within(subtotal + tax_amount, total, EXACT_MATCH_TOLERANCE):
            issues.append(
                _error(
                    IssueCode.TOTAL_MISMATCH,
                    "total",
                    f"Total {total} does not equal subtotal + tax ({subtotal} + {tax_amount} = "
                    f"{subtotal + tax_amount}) (exact match required).",
                )
            )
    if invoice.tax_rate is not None:
        rate = invoice.tax_rate
        if rate < MIN_TAX_RATE or rate > MAX_TAX_RATE:
            issues.append(
                _error(
                    IssueCode.TAX_RATE_OUT_OF_RANGE,
                    "tax_rate",
                    f"Tax rate {rate} is outside the range {MIN_TAX_RATE}-{MAX_TAX_RATE} %.",
                )
            )
        elif subtotal is not None and tax_amount is not None:
            expected_tax = subtotal * rate / HUNDRED
            if not _within(expected_tax, tax_amount, tolerance):
                issues.append(
                    _error(
                        IssueCode.TAX_AMOUNT_MISMATCH,
                        "tax_amount",
                        f"Tax amount {tax_amount} differs from subtotal x rate "
                        f"({subtotal} x {rate} % = {expected_tax}) by more than {tolerance}.",
                    )
                )
    if total is not None and total == ZERO and invoice.line_items:
        issues.append(
            _error(IssueCode.ZERO_TOTAL, "total", "Total is zero although line items exist.")
        )


def _check_dates(invoice: Invoice, today: date, issues: list[Issue]) -> None:
    """Rule i: due date not before invoice date; invoice date not in the future."""
    invoice_date, due_date = invoice.invoice_date, invoice.due_date
    if invoice_date is not None and due_date is not None and due_date < invoice_date:
        issues.append(
            _error(
                IssueCode.DUE_BEFORE_INVOICE_DATE,
                "due_date",
                f"Due date {due_date} is before invoice date {invoice_date}.",
            )
        )
    if invoice_date is not None and invoice_date > today:
        issues.append(
            _error(
                IssueCode.INVOICE_DATE_IN_FUTURE,
                "invoice_date",
                f"Invoice date {invoice_date} is after today ({today}).",
            )
        )


def _check_iban(invoice: Invoice, issues: list[Issue]) -> None:
    """Rule j: IBAN shape, mod-97 check digits, then registry length and structure."""
    if invoice.iban is None or not invoice.iban.strip():
        return
    iban = normalize_iban(invoice.iban)
    result = check_iban(iban)
    if not result.well_formed:
        issues.append(
            _error(
                IssueCode.INVALID_IBAN,
                "iban",
                "IBAN must be two letters, two check digits and letters/digits.",
            )
        )
        return
    if not result.checksum_ok:
        issues.append(
            _error(IssueCode.INVALID_IBAN, "iban", "IBAN check digits (mod 97) do not match.")
        )
        return
    if result.spec is None:
        issues.append(
            _warning(
                IssueCode.IBAN_LENGTH_UNVERIFIED,
                "iban",
                f"IBAN length and structure for country {result.country} were not verified "
                "(country not in the IBAN registry).",
            )
        )
        return
    if not result.length_ok:
        issues.append(
            _error(
                IssueCode.INVALID_IBAN,
                "iban",
                f"IBAN for {result.country} must be {result.spec.length} characters long "
                f"(registry), got {len(iban)}.",
            )
        )
    elif not result.structure_ok:
        issues.append(
            _error(
                IssueCode.INVALID_IBAN,
                "iban",
                f"IBAN does not match the registry structure for {result.country} "
                f"({result.spec.structure}).",
            )
        )


def _check_tax_ids(invoice: Invoice, issues: list[Issue]) -> None:
    """Rule k: tax ID format by country prefix (EU VAT) or EIN."""
    for party_name in _PARTIES:
        party = getattr(invoice, party_name)
        if party.tax_id is None or not party.tax_id.strip():
            continue
        path = f"{party_name}.tax_id"
        status = check_tax_id(normalize_tax_id(party.tax_id))
        if status is TaxIdStatus.INVALID:
            issues.append(
                _error(
                    IssueCode.INVALID_TAX_ID,
                    path,
                    f"Tax ID '{party.tax_id}' does not match the format for its country prefix.",
                )
            )
        elif status is TaxIdStatus.UNKNOWN:
            issues.append(
                _warning(
                    IssueCode.TAX_ID_UNVERIFIED,
                    path,
                    f"Tax ID '{party.tax_id}' has an unrecognized format and was not verified.",
                )
            )
