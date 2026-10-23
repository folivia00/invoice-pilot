"""IBAN checks per ISO 13616: shape, mod-97 check digits, registry length and structure.

Per-country length and structure come from ``iban_registry.py``, generated
from the SWIFT IBAN Registry, Release 103 - Sep 2026 (SWIFT is the ISO 13616
registration authority; registry page
https://www.swift.com/standards/data-standards/iban-international-bank-account-number,
checked 2026-10-23). Countries absent from the registry get the mod-97 check
only and are reported as "length not verified".

Check-digit algorithm (ISO 13616-1): move the first four characters to the end,
replace each letter by two digits (A=10 ... Z=35), and the resulting integer
modulo 97 must equal 1.
"""

import re
from dataclasses import dataclass
from typing import Final

from invoice_pilot.domain.iban_registry import IBAN_REGISTRY, IBAN_REGISTRY_RELEASE, IbanSpec

__all__ = [
    "IBAN_REGISTRY",
    "IBAN_REGISTRY_RELEASE",
    "IbanCheck",
    "IbanSpec",
    "check_iban",
    "compute_check_digits",
    "mod97",
]

#: Two letters (country), two check digits, then the BBAN (letters and digits).
_IBAN_SHAPE: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]+$")


@dataclass(frozen=True, slots=True)
class IbanCheck:
    """Outcome of ``check_iban`` for an already normalized IBAN."""

    well_formed: bool
    checksum_ok: bool
    country: str | None
    #: Registry entry for the country, or None when the country is not listed.
    spec: IbanSpec | None
    #: True/False against the registry length; None when the country is unknown.
    length_ok: bool | None
    #: True/False against the registry structure; None when the country is unknown.
    structure_ok: bool | None

    @property
    def ok(self) -> bool:
        """True when nothing is wrong (an unknown country still counts as ok)."""
        return (
            self.well_formed
            and self.checksum_ok
            and self.length_ok is not False
            and self.structure_ok is not False
        )


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
    """Check shape, check digits and, when the country is in the registry, length and structure."""
    if not _IBAN_SHAPE.match(iban):
        return IbanCheck(
            well_formed=False,
            checksum_ok=False,
            country=None,
            spec=None,
            length_ok=None,
            structure_ok=None,
        )
    country = iban[:2]
    checksum_ok = mod97(iban) == 1
    spec = IBAN_REGISTRY.get(country)
    if spec is None:
        return IbanCheck(
            well_formed=True,
            checksum_ok=checksum_ok,
            country=country,
            spec=None,
            length_ok=None,
            structure_ok=None,
        )
    return IbanCheck(
        well_formed=True,
        checksum_ok=checksum_ok,
        country=country,
        spec=spec,
        length_ok=len(iban) == spec.length,
        structure_ok=spec.pattern.match(iban) is not None,
    )
