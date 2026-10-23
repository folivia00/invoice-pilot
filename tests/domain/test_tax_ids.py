"""Tax ID format checks against the VIES structure table and the IRS EIN format."""

import pytest

from invoice_pilot.domain.normalize import normalize_tax_id
from invoice_pilot.domain.tax_ids import VAT_ID_PATTERNS, TaxIdStatus, check_tax_id


@pytest.mark.parametrize(
    "value",
    [
        "DE123456789",
        "ATU12345678",
        "FR12345678901",
        "FRAB123456789",
        "NL123456789B01",
        "NL12345678*B9+",
        "BE0123456789",
        "BE1123456789",
        "IT12345678901",
        "ESA1234567B",
        "ESA12345678",
        "ES12345678Z",
        "PL1234567890",
        "DK12345678",
        "SE123456789012",
        "12-3456789",
    ],
)
def test_valid_formats(value: str) -> None:
    assert check_tax_id(value) is TaxIdStatus.VALID


@pytest.mark.parametrize(
    "value",
    [
        "DE12345678",  # 8 digits instead of 9
        "DE1234567890",
        "AT123456789",  # missing the mandatory U
        "FR1234567890",  # body too short
        "NL123456789A01",  # 10th position must be B
        "BE2123456789",  # must start with 0 or 1
        "IT1234567890",
        "ES123456789",  # first and last both numeric
        "PL123456789",
        "DK1234567",
        "SE12345678901",
    ],
)
def test_invalid_formats(value: str) -> None:
    assert check_tax_id(value) is TaxIdStatus.INVALID


@pytest.mark.parametrize("value", ["123456789", "US123456789", "IE1234567FA", "CHE123456789"])
def test_unknown_prefix_is_unverified(value: str) -> None:
    assert check_tax_id(value) is TaxIdStatus.UNKNOWN


def test_minimum_required_countries_are_covered() -> None:
    assert {"DE", "AT", "FR", "NL", "BE", "IT", "ES", "PL"} <= set(VAT_ID_PATTERNS)


def test_check_after_normalization() -> None:
    assert check_tax_id(normalize_tax_id("de 123 456 789")) is TaxIdStatus.VALID
    assert check_tax_id(normalize_tax_id("NL 8023.95.123.B01")) is TaxIdStatus.VALID
    assert check_tax_id(normalize_tax_id("12-3456789")) is TaxIdStatus.VALID
    # An EIN written without the hyphen is not the official format.
    assert check_tax_id(normalize_tax_id("123456789")) is TaxIdStatus.UNKNOWN
