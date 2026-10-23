"""ISO 4217 reference table (generated from the SIX list-one.xml)."""

import pytest

from invoice_pilot.domain.currencies import (
    CURRENCY_MINOR_UNITS,
    UNSUPPORTED_CURRENCY_CODES,
    minor_units,
)


@pytest.mark.parametrize(
    ("code", "units"), [("EUR", 2), ("USD", 2), ("GBP", 2), ("JPY", 0), ("BHD", 3), ("CHF", 2)]
)
def test_known_minor_units(code: str, units: int) -> None:
    assert minor_units(code) == units


@pytest.mark.parametrize("code", ["XAU", "XAG", "XDR", "XXX", "XTS", "CLF", "BOV", "USN"])
def test_unsupported_codes_are_not_valid_for_invoices(code: str) -> None:
    assert minor_units(code) is None
    assert code in UNSUPPORTED_CURRENCY_CODES


def test_tables_are_disjoint_and_well_formed() -> None:
    assert not set(CURRENCY_MINOR_UNITS) & set(UNSUPPORTED_CURRENCY_CODES)
    assert all(len(code) == 3 and code.isupper() for code in CURRENCY_MINOR_UNITS)
    assert set(CURRENCY_MINOR_UNITS.values()) <= {0, 2, 3, 4}
    assert len(CURRENCY_MINOR_UNITS) > 100


def test_unknown_code_is_unsupported() -> None:
    assert minor_units("EUR ") is None
    assert minor_units("eur") is None
    assert minor_units("ABC") is None
