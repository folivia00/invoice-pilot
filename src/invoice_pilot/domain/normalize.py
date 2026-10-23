"""Normalization helpers for identifiers extracted by the LLM.

These are plain functions on purpose: the models store what was extracted, and
the validation rules normalize on the fly. Nothing here mutates a model.
"""

import re
from typing import Final

#: US Employer Identification Number as written by the IRS: "12-3456789".
#: Source: Instructions for Form SS-4 (Rev. 12/2025), https://www.irs.gov/instructions/iss4
#: ("An EIN is a 9-digit number (for example, 12-3456789)"). Checked 2026-10-23.
EIN_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{2}-\d{7}$")

_SEPARATORS: Final[re.Pattern[str]] = re.compile(r"[\s.\-]")


def normalize_iban(value: str) -> str:
    """Upper-case the IBAN and drop all whitespace (printed IBANs use groups of 4)."""
    return "".join(value.split()).upper()


def normalize_currency(value: str) -> str:
    """Upper-case and strip a currency code."""
    return value.strip().upper()


def normalize_tax_id(value: str) -> str:
    """Upper-case and drop spaces, dots and hyphens; an EIN keeps its hyphen."""
    stripped = value.strip()
    if EIN_PATTERN.match(stripped):
        return stripped
    return _SEPARATORS.sub("", stripped).upper()
