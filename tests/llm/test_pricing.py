"""Price table and per-call cost."""

from datetime import date
from decimal import Decimal

import pytest

from invoice_pilot.llm.pricing import PRICES, cost_usd, price_for
from invoice_pilot.llm.types import UsageRecord


def _usage(model: str, input_tokens: int, output_tokens: int, thinking: int = 0) -> UsageRecord:
    return UsageRecord(
        provider="x",
        model=model,
        status="ok",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking,
    )


def test_every_price_has_source_and_check_date() -> None:
    assert {p.model for p in PRICES} == {"gemini-3.5-flash-lite", "gemini-3.8-flash", "gpt-6-luna"}
    for price in PRICES:
        assert price.source.startswith("https://")
        assert price.checked_on == date(2026, 10, 24)
        assert price.input_usd_per_1m > 0 and price.output_usd_per_1m > 0


@pytest.mark.parametrize(
    ("model", "day", "expected"),
    [
        ("gemini-3.5-flash-lite", date(2026, 10, 24), Decimal("2.80")),  # 0.30 + 2.50
        ("gemini-3.8-flash", date(2026, 12, 31), Decimal("4.50")),  # 0.75 + 3.75
        ("gemini-3.8-flash", date(2027, 1, 1), Decimal("9.00")),  # 1.50 + 7.50
        ("gpt-6-luna", date(2026, 10, 24), Decimal("0.60")),  # 0.10 + 0.50
    ],
)
def test_cost_per_million_tokens(model: str, day: date, expected: Decimal) -> None:
    assert cost_usd(_usage(model, 1_000_000, 1_000_000), day) == expected


def test_cost_is_exact_decimal_for_small_calls() -> None:
    cost = cost_usd(_usage("gemini-3.5-flash-lite", 1200, 350), date(2026, 10, 24))
    assert cost == Decimal("0.000360") + Decimal("0.000875")
    assert isinstance(cost, Decimal)


def test_thinking_tokens_are_already_inside_output_tokens() -> None:
    with_thinking = _usage("gemini-3.8-flash", 1000, 500, thinking=200)
    without = _usage("gemini-3.8-flash", 1000, 500)
    day = date(2026, 11, 1)
    assert cost_usd(with_thinking, day) == cost_usd(without, day)


def test_price_switch_dates() -> None:
    assert price_for("gemini-3.8-flash", date(2026, 12, 31)).input_usd_per_1m == Decimal("0.75")
    assert price_for("gemini-3.8-flash", date(2027, 1, 1)).input_usd_per_1m == Decimal("1.50")
    assert price_for("gemini-3.8-flash", date(2026, 1, 1)).valid_to == date(2026, 12, 31)


def test_unknown_model_raises() -> None:
    with pytest.raises(ValueError, match="no price configured"):
        cost_usd(_usage("gpt-99", 1, 1), date(2026, 10, 24))
