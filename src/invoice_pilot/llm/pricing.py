"""List prices per million tokens and the cost of one call.

Prices are copied from the official pages on the date given in ``checked_on``;
they are never taken from memory. Both providers bill thinking/reasoning tokens
as output tokens, and ``UsageRecord.output_tokens`` already includes them.
Cached-input discounts are not modelled in v1 (no caching is used).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from invoice_pilot.llm.types import UsageRecord

GEMINI_PRICING_URL: Final = "https://ai.google.dev/gemini-api/docs/pricing"
OPENAI_PRICING_URL: Final = "https://developers.openai.com/api/docs/pricing"
_CHECKED: Final = date(2026, 10, 24)
_MILLION: Final = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class Price:
    """USD per 1M tokens, valid on ``valid_from`` <= day <= ``valid_to`` (None = open)."""

    model: str
    input_usd_per_1m: Decimal
    output_usd_per_1m: Decimal
    source: str
    checked_on: date
    valid_from: date | None = None
    valid_to: date | None = None

    def applies_on(self, day: date) -> bool:
        after_start = self.valid_from is None or day >= self.valid_from
        before_end = self.valid_to is None or day <= self.valid_to
        return after_start and before_end


PRICES: Final[tuple[Price, ...]] = (
    Price("gemini-3.5-flash-lite", Decimal("0.30"), Decimal("2.50"), GEMINI_PRICING_URL, _CHECKED),
    # Introductory price until 2026-12-31, standard price from 2027-01-01.
    Price(
        "gemini-3.8-flash",
        Decimal("0.75"),
        Decimal("3.75"),
        GEMINI_PRICING_URL,
        _CHECKED,
        valid_to=date(2026, 12, 31),
    ),
    Price(
        "gemini-3.8-flash",
        Decimal("1.50"),
        Decimal("7.50"),
        GEMINI_PRICING_URL,
        _CHECKED,
        valid_from=date(2027, 1, 1),
    ),
    Price("gpt-6-luna", Decimal("0.10"), Decimal("0.50"), OPENAI_PRICING_URL, _CHECKED),
)


def price_for(model: str, on_date: date) -> Price:
    """Price row of ``model`` valid on ``on_date``; ValueError if none is configured."""
    for price in PRICES:
        if price.model == model and price.applies_on(on_date):
            return price
    raise ValueError(f"no price configured for model {model!r} on {on_date.isoformat()}")


def cost_usd(usage: UsageRecord, on_date: date) -> Decimal:
    """Exact cost of one call in USD (no rounding; round when presenting)."""
    price = price_for(usage.model, on_date)
    return (
        Decimal(usage.input_tokens) * price.input_usd_per_1m
        + Decimal(usage.output_tokens) * price.output_usd_per_1m
    ) / _MILLION
