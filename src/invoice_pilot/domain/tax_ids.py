"""Format checks for tax identification numbers (no check digits).

EU VAT identification numbers
    Source: European Commission, VIES on-the-Web, FAQ Q11 "VAT identification
    number structure" (table rendered by https://ec.europa.eu/taxation_customs/vies/
    from its official text resource .../vies/assets/i18n/en.json, keys
    faq_label_<CC>format / faq_lbl_<CC>formatdesc). Checked 2026-10-23.
    Legend from the same table: 9 = a digit, X = a letter or a digit,
    S = a letter, a digit, "+" or "*", L = a letter; notes: AT position 1 is
    always "U"; ES first and last characters may be alpha or numeric but not
    both numeric; NL 10th position is always "B". Patterns apply to the
    normalized value (upper case, no spaces/dots/hyphens). Ireland is left out
    because the table notation for IE ("IE9S99999L" / "IE9999999WI") is ambiguous
    about literal letters; IE numbers therefore get the "unverified" warning.

US Employer Identification Number
    Source: IRS, Instructions for Form SS-4 (Rev. 12/2025),
    https://www.irs.gov/instructions/iss4 -- "An EIN is a 9-digit number
    (for example, 12-3456789)". Checked 2026-10-23.
"""

import re
from enum import Enum
from typing import Final

from invoice_pilot.domain.normalize import EIN_PATTERN

#: Country prefix -> full-match pattern of the normalized VAT number (prefix included).
VAT_ID_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    code: re.compile(f"^{body}$")
    for code, body in {
        "AT": r"ATU\d{8}",
        "BE": r"BE[01]\d{9}",
        "BG": r"BG\d{9,10}",
        "CY": r"CY\d{8}[A-Z]",
        "CZ": r"CZ\d{8,10}",
        "DE": r"DE\d{9}",
        "DK": r"DK\d{8}",
        "EE": r"EE\d{9}",
        "EL": r"EL\d{9}",
        "ES": r"ES(?:[A-Z]\d{7}[A-Z0-9]|\d{8}[A-Z])",
        "FI": r"FI\d{8}",
        "FR": r"FR[A-Z0-9]{2}\d{9}",
        "GB": r"GB(?:\d{9}|\d{12}|GD\d{3}|HA\d{3})",
        "HR": r"HR\d{11}",
        "HU": r"HU\d{8}",
        "IT": r"IT\d{11}",
        "LT": r"LT(?:\d{9}|\d{12})",
        "LU": r"LU\d{8}",
        "LV": r"LV\d{11}",
        "MT": r"MT\d{8}",
        "NL": r"NL[A-Z0-9+*]{9}B[A-Z0-9+*]{2}",
        "PL": r"PL\d{10}",
        "PT": r"PT\d{9}",
        "RO": r"RO\d{2,10}",
        "SE": r"SE\d{12}",
        "SI": r"SI\d{8}",
        "SK": r"SK\d{10}",
        "XI": r"XI(?:\d{9}|\d{12}|GD\d{3}|HA\d{3})",
    }.items()
}


class TaxIdStatus(Enum):
    """Result of a tax ID format check."""

    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"


def check_tax_id(normalized: str) -> TaxIdStatus:
    """Classify a normalized tax ID by prefix (EU VAT) or EIN shape.

    VALID: matches the official format for its prefix or is an EIN.
    INVALID: has a known EU prefix but does not match that country's format.
    UNKNOWN: no known prefix and not an EIN (format cannot be checked).
    """
    if EIN_PATTERN.match(normalized):
        return TaxIdStatus.VALID
    pattern = VAT_ID_PATTERNS.get(normalized[:2])
    if pattern is None:
        return TaxIdStatus.UNKNOWN
    return TaxIdStatus.VALID if pattern.match(normalized) else TaxIdStatus.INVALID
