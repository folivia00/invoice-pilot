"""Model tests: Decimal parsing from JSON and Python input, defaults, immutability."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from invoice_pilot.domain import Invoice, LineItem, Party


def test_decimal_from_json_number_is_exact() -> None:
    invoice = Invoice.model_validate_json('{"total": 19.99}')
    assert invoice.total == Decimal("19.99")
    assert str(invoice.total) == "19.99"


def test_decimal_from_json_string_is_exact() -> None:
    invoice = Invoice.model_validate_json('{"total": "19.99"}')
    assert invoice.total == Decimal("19.99")
    assert str(invoice.total) == "19.99"


def test_decimal_from_python_float_has_no_binary_artifacts() -> None:
    invoice = Invoice.model_validate({"total": 19.99, "subtotal": 2.675})
    assert str(invoice.total) == "19.99"
    assert str(invoice.subtotal) == "2.675"


def test_decimal_from_python_string_and_decimal() -> None:
    invoice = Invoice.model_validate({"total": "0.10", "subtotal": Decimal("0.10")})
    assert invoice.total == Decimal("0.10")
    assert invoice.subtotal == Decimal("0.10")


def test_nested_json_decimals_and_dates() -> None:
    payload = (
        '{"invoice_date": "2026-10-01", "line_items": '
        '[{"description": "x", "quantity": 1.5, "unit_price": "3.30", "amount": 4.95}]}'
    )
    invoice = Invoice.model_validate_json(payload)
    assert invoice.invoice_date.isoformat() == "2026-10-01"
    item = invoice.line_items[0]
    assert (item.quantity, item.unit_price, item.amount) == (
        Decimal("1.5"),
        Decimal("3.30"),
        Decimal("4.95"),
    )


def test_all_extracted_fields_are_optional() -> None:
    invoice = Invoice()
    assert invoice.supplier == Party()
    assert invoice.buyer == Party()
    assert invoice.line_items == []
    assert invoice.total is None
    assert LineItem().amount is None


def test_models_are_frozen_and_reject_unknown_fields() -> None:
    invoice = Invoice(total=Decimal("1"))
    with pytest.raises(ValidationError):
        invoice.total = Decimal("2")  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Invoice.model_validate({"grand_total": "1"})
