"""IBAN structure and mod-97 checks.

Official example IBANs from the SWIFT IBAN Registry could not be retrieved on
2026-10-23 (swift.com answered 403 to every request), so the valid examples
below are constructed with ``compute_check_digits`` and the invalid ones are
derived from them by changing one digit or swapping two adjacent digits.
Extend with registry examples once the registry file is available.
"""

import pytest

from invoice_pilot.domain.iban import (
    IBAN_LENGTHS,
    check_iban,
    compute_check_digits,
    mod97,
)


def build(country: str, bban: str) -> str:
    return country + compute_check_digits(country, bban) + bban


VALID = [
    build("DE", "370400440532013000"),
    build("FR", "20041010050500013M02606"),
    build("GB", "NWBK60161331926819"),
    build("AT", "1904300234573201"),
    build("NL", "ABNA0417164300"),
    build("CH", "00762011623852957"),
]


@pytest.mark.parametrize("iban", VALID)
def test_constructed_iban_passes_mod97(iban: str) -> None:
    result = check_iban(iban)
    assert mod97(iban) == 1
    assert result.well_formed and result.checksum_ok
    assert result.country == iban[:2]


@pytest.mark.parametrize("iban", VALID)
def test_single_digit_change_breaks_checksum(iban: str) -> None:
    # Change the last digit of the BBAN.
    pos = next(i for i in range(len(iban) - 1, 3, -1) if iban[i].isdigit())
    changed = iban[:pos] + str((int(iban[pos]) + 1) % 10) + iban[pos + 1 :]
    assert changed != iban
    assert not check_iban(changed).checksum_ok


@pytest.mark.parametrize("iban", VALID)
def test_adjacent_transposition_breaks_checksum(iban: str) -> None:
    # Find two different adjacent digits in the BBAN and swap them.
    pos = next(
        i
        for i in range(4, len(iban) - 1)
        if iban[i].isdigit() and iban[i + 1].isdigit() and iban[i] != iban[i + 1]
    )
    swapped = iban[:pos] + iban[pos + 1] + iban[pos] + iban[pos + 2 :]
    assert swapped != iban
    assert not check_iban(swapped).checksum_ok


@pytest.mark.parametrize("bad", ["DE4450010517540732493", "1234", "DEXX500105175407324931", ""])
def test_malformed_iban(bad: str) -> None:
    result = check_iban(bad)
    assert (result.well_formed, result.checksum_ok) in {(False, False), (True, False)}


def test_check_digits_are_two_digits_and_never_00_or_01() -> None:
    for bban in ("0", "1", "99", "ABC123", "370400440532013000"):
        digits = compute_check_digits("DE", bban)
        assert len(digits) == 2 and digits.isdigit()
        assert digits not in {"00", "01"}


def test_length_table_state_is_explicit() -> None:
    # Until the SWIFT registry is available the table is empty and lengths are unverified.
    iban = VALID[0]
    result = check_iban(iban)
    if iban[:2] in IBAN_LENGTHS:
        assert result.length_ok is True
    else:
        assert result.length_ok is None
