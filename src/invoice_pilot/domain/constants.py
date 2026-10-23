"""Named rounding and tolerance constants shared by all validation rules.

Everything monetary is Decimal. Tolerances are expressed in minor units of the
invoice currency (see ``currencies.py``) so that EUR gets 0.01 and JPY gets 1.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Final

#: Allowed absolute difference for every monetary comparison, in minor units.
TOLERANCE_MINOR_UNITS: Final[int] = 1

#: Minor units assumed when the currency is missing or unsupported. Using 2
#: keeps the arithmetic rules meaningful without hiding the currency error.
FALLBACK_MINOR_UNITS: Final[int] = 2

#: Rounding mode used when computing expected amounts (tax, line totals).
ROUNDING: Final[str] = ROUND_HALF_UP

#: Tax rate is a percentage; anything outside this closed range is an error.
MIN_TAX_RATE: Final[Decimal] = Decimal(0)
MAX_TAX_RATE: Final[Decimal] = Decimal(100)

ZERO: Final[Decimal] = Decimal(0)
HUNDRED: Final[Decimal] = Decimal(100)


def tolerance_for(minor_units: int) -> Decimal:
    """Return the monetary tolerance for a currency with ``minor_units`` decimals."""
    return Decimal(TOLERANCE_MINOR_UNITS).scaleb(-minor_units)


def quantize_to(value: Decimal, minor_units: int) -> Decimal:
    """Round ``value`` to ``minor_units`` decimals using the project rounding mode."""
    return value.quantize(Decimal(1).scaleb(-minor_units), rounding=ROUNDING)
