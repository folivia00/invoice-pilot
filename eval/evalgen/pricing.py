"""Line item prices and quantities: realistic per-item ranges, scaled per currency.

Every catalog entry in ``eval/dataset/locales/*.json`` carries a unit price
range in EUR (``price_min``/``price_max``) and, for weight and length items, a
maximum quantity (``qty_max``). The ranges are identical in both languages so
EN and DE documents of the same item are priced alike.
"""

import random
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Final

from invoice_pilot.domain.constants import quantize_to

#: Scale factors applied to the EUR price ranges of the catalog. These are NOT
#: exchange rates and are never updated: they only keep printed prices at a
#: plausible magnitude for each currency (and make JPY amounts integers of a
#: sensible size). The EUR equivalent of any amount is ``amount / scale``.
CURRENCY_SCALE: Final[dict[str, Decimal]] = {
    "EUR": Decimal("1"),
    "USD": Decimal("1.1"),
    "GBP": Decimal("0.85"),
    "CHF": Decimal("0.95"),
    "JPY": Decimal("160"),
}

#: Dataset invariant: every invoice total, in EUR equivalent, lies in this range.
TOTAL_EUR_MIN: Final[Decimal] = Decimal(20)
TOTAL_EUR_MAX: Final[Decimal] = Decimal(60_000)

#: Share of unit prices printed as whole currency units (x.00).
ROUND_PRICE_SHARE: Final[float] = 0.3


def eur_equivalent(amount: Decimal, currency: str) -> Decimal:
    """Convert an amount in ``currency`` back to the EUR scale of the catalog."""
    return amount / CURRENCY_SCALE[currency]


def price_bounds(entry: dict[str, Any], currency: str, units: int) -> tuple[Decimal, Decimal]:
    """EUR range of ``entry`` scaled to ``currency`` and snapped inwards to its minor units."""
    scale = CURRENCY_SCALE[currency]
    step = Decimal(1).scaleb(-units)
    low = (Decimal(str(entry["price_min"])) * scale).quantize(step, rounding=ROUND_CEILING)
    high = (Decimal(str(entry["price_max"])) * scale).quantize(step, rounding=ROUND_FLOOR)
    assert low <= high, entry
    return low, high


def unit_price(rng: random.Random, entry: dict[str, Any], currency: str, units: int) -> Decimal:
    """Unit price inside the scaled range of ``entry``; about 30 % are round (x.00)."""
    low, high = price_bounds(entry, currency, units)
    value = rng.uniform(float(low), float(high))
    if rng.random() < ROUND_PRICE_SHARE:
        value = float(int(value))  # whole currency units; clamped back below if out of range
    return min(max(quantize_to(Decimal(repr(value)), units), low), high)


def quantity(rng: random.Random, entry: dict[str, Any]) -> Decimal:
    """Realistic quantity for the unit type of ``entry``.

    piece: 80 % in 1-10, otherwise 1-50; hour: quarter-hour steps, 80 % in
    0.5-40, otherwise 0.5-80; weight/length: 80 % within a fifth of the entry's
    ``qty_max``, otherwise up to ``qty_max``, with up to two decimals; flat
    (one-off service, subscription): always 1.
    """
    unit_type = entry["unit"]
    if unit_type == "piece":
        return Decimal(rng.randint(1, 10) if rng.random() < 0.8 else rng.randint(1, 50))
    if unit_type == "hour":
        quarters = rng.randint(2, 160) if rng.random() < 0.8 else rng.randint(2, 320)
        return Decimal(quarters) / 4  # exact division keeps the natural number of decimals
    if unit_type in ("weight", "length"):
        limit = int(entry["qty_max"])
        if rng.random() < 0.8:
            limit //= 5
        places = rng.choice((0, 1, 2, 2))
        if places == 0:
            return Decimal(rng.randint(1, limit))
        whole = rng.randint(1, limit - 1)
        return Decimal(f"{whole}.{rng.randint(1, 10**places - 1):0{places}d}")
    assert unit_type == "flat", unit_type
    return Decimal(1)
