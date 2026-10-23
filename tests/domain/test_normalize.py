"""Normalization helpers."""

import pytest

from invoice_pilot.domain.normalize import normalize_currency, normalize_iban, normalize_tax_id


def test_normalize_iban_removes_spaces_and_uppercases() -> None:
    assert normalize_iban(" de44 5001 0517 5407 3249 31 ") == "DE44500105175407324931"


@pytest.mark.parametrize(("raw", "expected"), [("eur", "EUR"), (" Jpy ", "JPY")])
def test_normalize_currency(raw: str, expected: str) -> None:
    assert normalize_currency(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("de 123.456-789", "DE123456789"),
        ("atu 12345678", "ATU12345678"),
        ("NL 8023.95.123.B01", "NL802395123B01"),
        ("12-3456789", "12-3456789"),
        (" 12-3456789 ", "12-3456789"),
    ],
)
def test_normalize_tax_id(raw: str, expected: str) -> None:
    assert normalize_tax_id(raw) == expected
