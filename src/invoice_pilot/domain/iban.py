"""IBAN checks per ISO 13616: mod-97 check digits and per-country length.

Country lengths must come from the official IBAN Registry published by SWIFT
(the ISO 13616 registration authority):
    https://www.swift.com/standards/data-standards/iban-international-bank-account-number
On 2026-10-23 every automated request to swift.com (registry page, TXT and PDF
downloads) was answered with HTTP 403 "SWIFT site off-line", so the registry
could not be retrieved. Per project rules reference data is never filled from
memory, therefore ``IBAN_LENGTHS`` is intentionally empty until the registry
file is obtained. Until then ``check_iban`` verifies structure and check digits
only and reports the length as unverified for every country.

Check-digit algorithm (ISO 13616-1): move the first four characters to the end,
replace each letter by two digits (A=10 ... Z=35), and the resulting integer
modulo 97 must equal 1.
"""

import re
from dataclasses import dataclass
from typing import Final

#: Country code -> total IBAN length. Fill from the SWIFT IBAN Registry only.
IBAN_LENGTHS: Final[dict[str, int]] = {}

#: Registry release the lengths were taken from; None until the file is used.
IBAN_REGISTRY_RELEASE: Final[str | None] = None

#: Two letters (country), two check digits, then the BBAN (letters and digits).
_IBAN_SHAPE: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]+$")


@dataclass(frozen=True, slots=True)
class IbanCheck:
    """Outcome of ``check_iban`` for an already normalized IBAN."""

    well_formed: bool
    checksum_ok: bool
    country: str | None
    #: True = length matches the registry, False = mismatch, None = country unknown.
    length_ok: bool | None


def _to_numeric(rearranged: str) -> int:
    digits = []
    for ch in rearranged:
        digits.append(str(ord(ch) - 55) if ch.isalpha() else ch)
    return int("".join(digits))


def mod97(iban: str) -> int:
    """Return the ISO 13616 mod-97 remainder of a normalized IBAN."""
    return _to_numeric(iban[4:] + iban[:4]) % 97


def compute_check_digits(country: str, bban: str) -> str:
    """Return the two check digits that make ``country + digits + bban`` valid."""
    remainder = _to_numeric(bban + country + "00") % 97
    return f"{98 - remainder:02d}"


def check_iban(iban: str) -> IbanCheck:
    """Check structure, check digits and (when known) country length of an IBAN."""
    if not _IBAN_SHAPE.match(iban):
        return IbanCheck(well_formed=False, checksum_ok=False, country=None, length_ok=None)
    country = iban[:2]
    checksum_ok = mod97(iban) == 1
    expected_length = IBAN_LENGTHS.get(country)
    length_ok = None if expected_length is None else len(iban) == expected_length
    return IbanCheck(
        well_formed=True, checksum_ok=checksum_ok, country=country, length_ok=length_ok
    )
