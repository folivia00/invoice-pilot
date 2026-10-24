"""Field-by-field comparison of an extracted invoice with the ground truth.

Rules (docs/evaluation.md, "Normalization"): key fields are compared exactly
after canonicalization, free-text fields after a looser normalization; a null
on both sides is a match, a null against a value is a mismatch.
"""

import re
from collections.abc import Callable
from decimal import Decimal
from typing import Final

from invoice_pilot.domain import Invoice, LineItem
from invoice_pilot.domain.normalize import normalize_iban, normalize_tax_id

#: Key fields: part of the main metrics.
KEY_FIELDS: Final[tuple[str, ...]] = (
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "subtotal",
    "tax_rate",
    "tax_amount",
    "total",
    "iban",
    "bic",
    "supplier.name",
    "supplier.tax_id",
    "buyer.name",
    "buyer.tax_id",
    "line_items",
)

#: Free-text fields: reported separately, never part of the main metrics.
TEXT_FIELDS: Final[tuple[str, ...]] = (
    "supplier.address",
    "buyer.address",
    "line_items.description",
)

_TEXT_SEPARATORS: Final[re.Pattern[str]] = re.compile(r"[\r\n,]+")


def norm_whitespace(value: str) -> str:
    """Trim and collapse runs of whitespace to one space."""
    return " ".join(value.split())


def norm_name(value: str) -> str:
    """Names: whitespace collapsed, case ignored."""
    return norm_whitespace(value).casefold()


def norm_upper(value: str) -> str:
    """Currency codes: trimmed, upper case."""
    return value.strip().upper()


def norm_bic(value: str) -> str:
    """BIC: upper case, no whitespace."""
    return "".join(value.split()).upper()


def norm_text(value: str) -> str:
    """Free text: lower case, line breaks and commas become spaces, whitespace collapsed."""
    return norm_whitespace(_TEXT_SEPARATORS.sub(" ", value)).casefold()


def _same(value: object) -> object:
    return value


def _equal(expected: object, actual: object, norm: Callable[..., object] = _same) -> bool:
    if expected is None and actual is None:
        return True
    if expected is None or actual is None:
        return False
    return norm(expected) == norm(actual)


def _items_equal(expected: list[LineItem], actual: list[LineItem]) -> bool:
    if len(expected) != len(actual):
        return False
    return all(
        _equal(e.quantity, a.quantity)
        and _equal(e.unit_price, a.unit_price)
        and _equal(e.amount, a.amount)
        for e, a in zip(expected, actual, strict=True)
    )


def _descriptions_equal(expected: list[LineItem], actual: list[LineItem]) -> bool:
    if len(expected) != len(actual):
        return False
    return all(
        _equal(e.description, a.description, norm_text)
        for e, a in zip(expected, actual, strict=True)
    )


def compare_invoices(expected: Invoice, actual: Invoice | None) -> dict[str, bool]:
    """True/False per field of KEY_FIELDS and TEXT_FIELDS; all False when nothing was extracted."""
    if actual is None:
        return dict.fromkeys(KEY_FIELDS + TEXT_FIELDS, False)
    decimal: Callable[[Decimal], Decimal] = _same  # Decimal == is numeric: 19.9 == 19.90
    return {
        "invoice_number": _equal(expected.invoice_number, actual.invoice_number, norm_whitespace),
        "invoice_date": _equal(expected.invoice_date, actual.invoice_date),
        "due_date": _equal(expected.due_date, actual.due_date),
        "currency": _equal(expected.currency, actual.currency, norm_upper),
        "subtotal": _equal(expected.subtotal, actual.subtotal, decimal),
        "tax_rate": _equal(expected.tax_rate, actual.tax_rate, decimal),
        "tax_amount": _equal(expected.tax_amount, actual.tax_amount, decimal),
        "total": _equal(expected.total, actual.total, decimal),
        "iban": _equal(expected.iban, actual.iban, normalize_iban),
        "bic": _equal(expected.bic, actual.bic, norm_bic),
        "supplier.name": _equal(expected.supplier.name, actual.supplier.name, norm_name),
        "supplier.tax_id": _equal(
            expected.supplier.tax_id, actual.supplier.tax_id, normalize_tax_id
        ),
        "buyer.name": _equal(expected.buyer.name, actual.buyer.name, norm_name),
        "buyer.tax_id": _equal(expected.buyer.tax_id, actual.buyer.tax_id, normalize_tax_id),
        "line_items": _items_equal(expected.line_items, actual.line_items),
        "supplier.address": _equal(expected.supplier.address, actual.supplier.address, norm_text),
        "buyer.address": _equal(expected.buyer.address, actual.buyer.address, norm_text),
        "line_items.description": _descriptions_equal(expected.line_items, actual.line_items),
    }


def all_key_fields_correct(fields: dict[str, bool]) -> bool:
    return all(fields[name] for name in KEY_FIELDS)
