"""Named rounding and tolerance constants shared by all validation rules.

Everything monetary is Decimal. Two kinds of comparison exist:

* Rules e and f compare sums of amounts that are already rounded money values
  (sum of line amounts vs subtotal, subtotal + tax vs total). They must match
  exactly: a one-unit difference is an extraction error, not rounding.
* Rules d and g compare a computed value (quantity x unit price, subtotal x
  rate / 100) with a rounded extracted value. Half a minor unit of the currency
  is allowed (EUR 0.005, JPY 0.5, BHD 0.0005): that is exactly the maximum
  error of round-half-up, so a correctly rounded amount always passes and a
  one-unit error never does.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Final

#: Rules e and f: no tolerance at all.
EXACT_MATCH_TOLERANCE: Final[Decimal] = Decimal(0)

#: Rules d and g: allowed absolute difference, in minor units of the currency.
ROUNDING_TOLERANCE_UNITS: Final[Decimal] = Decimal("0.5")

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


def minor_unit(minor_units: int) -> Decimal:
    """Return one minor unit for a currency with ``minor_units`` decimals (EUR -> 0.01)."""
    return Decimal(1).scaleb(-minor_units)


def rounding_tolerance_for(minor_units: int) -> Decimal:
    """Return the rounding tolerance (half a minor unit) for rules d and g."""
    return ROUNDING_TOLERANCE_UNITS.scaleb(-minor_units)


def quantize_to(value: Decimal, minor_units: int) -> Decimal:
    """Round ``value`` to ``minor_units`` decimals using the project rounding mode."""
    return value.quantize(minor_unit(minor_units), rounding=ROUNDING)
