"""Synthetic identifiers: IBAN by registry structure, VAT numbers by VIES format, EIN, BIC.

All values are random and fictional; validity is checked against the domain
reference data so that clean invoices pass ``validate`` by construction.
"""

import random
import re
import string
from typing import Final

from invoice_pilot.domain.iban import IBAN_REGISTRY, compute_check_digits, mod97
from invoice_pilot.domain.tax_ids import TaxIdStatus, check_tax_id

_STRUCTURE_TOKEN: Final[re.Pattern[str]] = re.compile(r"(\d+)!([nac])")
_ALPHABETS: Final[dict[str, str]] = {
    "n": string.digits,
    "a": string.ascii_uppercase,
    "c": string.ascii_uppercase + string.digits,
}

#: Countries whose suppliers get an IBAN (all are in the SWIFT registry).
IBAN_COUNTRIES: Final[tuple[str, ...]] = (
    "DE",
    "AT",
    "FR",
    "NL",
    "GB",
    "CH",
    "IT",
    "ES",
    "PL",
    "BE",
)
#: Countries with a VAT number format in the VIES table (used for supplier/buyer tax IDs).
VAT_COUNTRIES: Final[tuple[str, ...]] = ("DE", "AT", "FR", "NL", "GB", "IT", "ES", "PL", "BE")


def digits(rng: random.Random, count: int) -> str:
    return "".join(rng.choice(string.digits) for _ in range(count))


def letters(rng: random.Random, count: int) -> str:
    return "".join(rng.choice(string.ascii_uppercase) for _ in range(count))


def make_iban(rng: random.Random, country: str) -> str:
    """Random IBAN following the registry structure of ``country`` with valid check digits."""
    tokens = _STRUCTURE_TOKEN.findall(IBAN_REGISTRY[country].structure[2:])
    bban = "".join(
        "".join(rng.choice(_ALPHABETS[kind]) for _ in range(int(n))) for n, kind in tokens[1:]
    )
    iban = country + compute_check_digits(country, bban) + bban
    assert mod97(iban) == 1 and IBAN_REGISTRY[country].pattern.match(iban)
    return iban


def corrupt_iban_check_digits(rng: random.Random, iban: str) -> str:
    """Return the same IBAN with check digits that fail mod-97 (structure and length kept)."""
    current = int(iban[2:4])
    for offset in rng.sample(range(1, 97), 96):
        candidate = (current + offset) % 100
        if candidate in (0, 1):
            continue
        corrupted = f"{iban[:2]}{candidate:02d}{iban[4:]}"
        if mod97(corrupted) != 1:
            return corrupted
    raise AssertionError("could not corrupt IBAN")


def make_vat_id(rng: random.Random, country: str) -> str:
    """Format-valid VAT number for a VIES country (no check digits are modelled)."""
    makers = {
        "DE": lambda: "DE" + digits(rng, 9),
        "AT": lambda: "ATU" + digits(rng, 8),
        "FR": lambda: "FR" + digits(rng, 2) + digits(rng, 9),
        "NL": lambda: "NL" + digits(rng, 9) + "B" + digits(rng, 2),
        "GB": lambda: "GB" + digits(rng, 9),
        "IT": lambda: "IT" + digits(rng, 11),
        "ES": lambda: "ES" + letters(rng, 1) + digits(rng, 7) + letters(rng, 1),
        "PL": lambda: "PL" + digits(rng, 10),
        "BE": lambda: "BE0" + digits(rng, 9),
    }
    value = makers[country]()
    assert check_tax_id(value) is TaxIdStatus.VALID, value
    return value


def make_bad_vat_id(rng: random.Random, country: str) -> str:
    """VAT number with the right prefix but a wrong body (one digit short)."""
    good = make_vat_id(rng, country)
    bad = good[:-1] if good[-1].isdigit() else good[:-2] + good[-1]
    assert check_tax_id(bad) is TaxIdStatus.INVALID, (good, bad)
    return bad


def make_ein(rng: random.Random) -> str:
    """US Employer Identification Number in the IRS format NN-NNNNNNN."""
    return f"{digits(rng, 2)}-{digits(rng, 7)}"


def make_bic(rng: random.Random, country: str) -> str:
    """Fictional 11-character BIC: 4 letters, country, location, branch."""
    location = rng.choice(["FF", "2L", "MM", "HH", "XX", "ZZ"])
    branch = rng.choice(["XXX", "100", "200", "B01"])
    return letters(rng, 4) + country + location + branch
