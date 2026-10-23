"""IBAN checks: official registry examples, mod-97, length and structure.

Valid examples are the "IBAN electronic format example" values of the SWIFT
IBAN Registry (Release 103 - Sep 2026) as generated into ``iban_registry.py``.
Invalid cases are derived from them (one digit changed, two adjacent digits
swapped, wrong length, wrong character class) with the check digits recomputed
where the point is to isolate the length/structure check from mod-97.
"""

import pytest

from invoice_pilot.domain.iban import (
    IBAN_REGISTRY,
    IBAN_REGISTRY_RELEASE,
    check_iban,
    compute_check_digits,
    mod97,
)
from invoice_pilot.domain.iban_registry import IBAN_EXAMPLES

#: Official examples that do not pass the checks, with the reason. Must stay empty
#: unless a registry example is demonstrably wrong; never "fix" the checks instead.
EXCLUDED_EXAMPLES: dict[str, str] = {}

EXAMPLES = sorted((c, e) for c, e in IBAN_EXAMPLES.items() if c not in EXCLUDED_EXAMPLES)


def rebuild(country: str, bban: str) -> str:
    """Return country + fresh check digits + bban (always passes mod-97)."""
    return country + compute_check_digits(country, bban) + bban


def test_registry_release_and_size() -> None:
    assert IBAN_REGISTRY_RELEASE == "Release 103 - Sep 2026"
    assert len(IBAN_REGISTRY) == 89
    assert set(IBAN_EXAMPLES) == set(IBAN_REGISTRY)
    for code in ("DE", "AT", "FR", "NL", "GB", "CH", "IT", "ES", "PL", "BE"):
        assert code in IBAN_REGISTRY


@pytest.mark.parametrize(("country", "example"), EXAMPLES)
def test_every_official_example_passes(country: str, example: str) -> None:
    result = check_iban(example)
    assert result.well_formed, example
    assert result.checksum_ok, example
    assert result.country == country
    assert result.spec is IBAN_REGISTRY[country]
    assert result.length_ok is True, (example, len(example), IBAN_REGISTRY[country].length)
    assert result.structure_ok is True, (example, IBAN_REGISTRY[country].structure)
    assert result.ok


@pytest.mark.parametrize(("country", "example"), EXAMPLES)
def test_single_digit_change_breaks_checksum(country: str, example: str) -> None:
    pos = next(i for i in range(len(example) - 1, 3, -1) if example[i].isdigit())
    changed = example[:pos] + str((int(example[pos]) + 1) % 10) + example[pos + 1 :]
    assert changed != example
    assert not check_iban(changed).checksum_ok


@pytest.mark.parametrize(("country", "example"), EXAMPLES)
def test_adjacent_transposition_breaks_checksum(country: str, example: str) -> None:
    pos = next(
        (
            i
            for i in range(4, len(example) - 1)
            if example[i].isdigit() and example[i + 1].isdigit() and example[i] != example[i + 1]
        ),
        None,
    )
    if pos is None:
        pytest.skip("example has no two different adjacent digits in the BBAN")
    swapped = example[:pos] + example[pos + 1] + example[pos] + example[pos + 2 :]
    assert swapped != example
    assert not check_iban(swapped).checksum_ok


def test_de_letter_in_bban_fails_structure_only() -> None:
    bban = IBAN_EXAMPLES["DE"][4:]
    bad = rebuild("DE", bban[:-1] + "A")  # DE structure is digits only
    result = check_iban(bad)
    assert result.checksum_ok and result.length_ok is True
    assert result.structure_ok is False and not result.ok


def test_de_length_21_fails_length() -> None:
    bban = IBAN_EXAMPLES["DE"][4:-1]  # one digit short
    bad = rebuild("DE", bban)
    result = check_iban(bad)
    assert len(bad) == 21 and result.checksum_ok
    assert result.length_ok is False and not result.ok


def test_nl_digit_in_bank_code_fails_structure() -> None:
    bban = IBAN_EXAMPLES["NL"][4:]  # ABNA0417164300: bank code must be 4 letters
    bad = rebuild("NL", "1" + bban[1:])
    result = check_iban(bad)
    assert result.checksum_ok and result.length_ok is True
    assert result.structure_ok is False and not result.ok


@pytest.mark.parametrize("bad", ["DE4450010517540732493", "1234", "DEXX500105175407324931", ""])
def test_malformed_iban(bad: str) -> None:
    result = check_iban(bad)
    assert not result.ok
    assert result.checksum_ok is False


def test_unknown_country_is_checksum_only() -> None:
    iban = rebuild("ZZ", "1234567890")
    result = check_iban(iban)
    assert mod97(iban) == 1 and result.checksum_ok
    assert result.spec is None and result.length_ok is None and result.structure_ok is None
    assert result.ok  # nothing provably wrong; validation reports a warning


def test_check_digits_are_two_digits_and_never_00_or_01() -> None:
    for bban in ("0", "1", "99", "ABC123", "370400440532013000"):
        digits = compute_check_digits("DE", bban)
        assert len(digits) == 2 and digits.isdigit()
        assert digits not in {"00", "01"}


def test_registry_state_is_explicit() -> None:
    # The registry is loaded: known countries are checked for length and structure,
    # unknown ones are not. (Replaces the "table is empty" state of task 05.)
    known = check_iban(IBAN_EXAMPLES["DE"])
    assert known.spec is not None and known.length_ok is True and known.structure_ok is True
    unknown = check_iban(rebuild("ZZ", "1234567890"))
    assert unknown.spec is None and unknown.length_ok is None
