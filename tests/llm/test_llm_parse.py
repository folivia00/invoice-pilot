"""Model text -> domain Invoice: exact decimals, strict shape, safe error messages."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest

from invoice_pilot.domain import Invoice
from invoice_pilot.llm.errors import InvalidResponseError
from invoice_pilot.llm.parse import parse_invoice


def test_valid_answer_becomes_domain_invoice(invoice_json: Callable[..., str]) -> None:
    invoice = parse_invoice(invoice_json())
    assert isinstance(invoice, Invoice)
    assert invoice.supplier.name == "Acme Ltd"
    assert invoice.invoice_date == date(2026, 9, 1)
    assert invoice.total == Decimal("240")
    assert invoice.line_items[0].unit_price == Decimal("100")
    assert invoice.iban is None


@pytest.mark.parametrize(
    ("literal", "expected"),
    [("0.1", Decimal("0.1")), ("1234.56", Decimal("1234.56")), ("19.99", Decimal("19.99"))],
)
def test_numbers_are_parsed_exactly(
    invoice_json: Callable[..., str], literal: str, expected: Decimal
) -> None:
    invoice = parse_invoice(invoice_json(total=Decimal(literal)))
    assert invoice.total == expected
    assert str(invoice.total) == literal  # no float artifacts such as 0.1000000000000000055


def test_exponent_notation_is_a_number(invoice_json: Callable[..., str]) -> None:
    text = invoice_json().replace('"total": 240', '"total": 1e2')
    assert '"total": 1e2' in text
    assert parse_invoice(text).total == Decimal(100)


def test_numeric_strings_are_accepted_as_decimals(invoice_json: Callable[..., str]) -> None:
    text = invoice_json().replace('"total": 240', '"total": "240.50"')
    assert parse_invoice(text).total == Decimal("240.50")


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("not json at all", "json_invalid"),
        ("[]", "model_type"),
        ('{"supplier": {}}', "missing"),
    ],
)
def test_invalid_json_or_shape_raises(text: str, fragment: str) -> None:
    with pytest.raises(InvalidResponseError) as info:
        parse_invoice(text)
    assert fragment in str(info.value)


def test_unknown_key_is_rejected(invoice_json: Callable[..., str]) -> None:
    with pytest.raises(InvalidResponseError) as info:
        parse_invoice(invoice_json(extra_field="x"))
    assert "extra_field: extra_forbidden" in str(info.value)


def test_missing_key_is_rejected(invoice_json: Callable[..., str]) -> None:
    text = invoice_json().replace('"bic": null', '"bic_": null')
    with pytest.raises(InvalidResponseError) as info:
        parse_invoice(text)
    assert "bic: missing" in str(info.value)


def test_non_numeric_amount_is_rejected(invoice_json: Callable[..., str]) -> None:
    with pytest.raises(InvalidResponseError) as info:
        parse_invoice(invoice_json(total="two hundred"))
    assert "total: decimal_parsing" in str(info.value)


def test_invalid_date_message_has_no_document_content(invoice_json: Callable[..., str]) -> None:
    text = invoice_json(
        invoice_date="31.09.2026",
        supplier={
            "name": "TOPSECRET GmbH",
            "tax_id": None,
            "address": "Hidden Lane 7",
        },
    )
    with pytest.raises(InvalidResponseError) as info:
        parse_invoice(text)
    message = str(info.value)
    assert "invoice_date" in message
    assert "31.09.2026" not in message and "TOPSECRET" not in message
    assert "Hidden Lane" not in message


def test_error_message_is_capped(invoice_json: Callable[..., str]) -> None:
    with pytest.raises(InvalidResponseError) as info:
        parse_invoice('{"supplier": {"name": null, "tax_id": null, "address": null}}')
    assert "(+" in str(info.value) and "more)" in str(info.value)
