"""Locale-dependent formatting of numbers, money and dates (as printed on the document)."""

from datetime import date
from decimal import Decimal
from typing import Final

#: number style -> (thousands separator, decimal separator)
NUMBER_STYLES: Final[dict[str, tuple[str, str]]] = {
    "en": (",", "."),
    "de": (".", ","),
    "ch": ("'", "."),
}

CURRENCY_SYMBOLS: Final[dict[str, str]] = {
    "EUR": "€",
    "GBP": "£",
    "USD": "$",
    "CHF": "CHF",
    "JPY": "¥",
}

DATE_FORMATS: Final[tuple[str, ...]] = ("dmy_dot", "iso", "mdy_slash", "long_en", "long_de")


def format_number(value: Decimal, decimals: int, style: str) -> str:
    """Format with fixed decimals and locale separators, e.g. 1.234,56 for 'de'."""
    thousands, decimal_sep = NUMBER_STYLES[style]
    quantized = value.quantize(Decimal(1).scaleb(-decimals)) if decimals > 0 else value.quantize(1)
    text = f"{quantized:,.{decimals}f}"  # 1,234.56
    return text.replace(",", "\x00").replace(".", decimal_sep).replace("\x00", thousands)


def format_quantity(value: Decimal, style: str) -> str:
    """Integers without decimals, other quantities with their own number of decimals."""
    if value == value.to_integral_value():
        return format_number(value, 0, style)
    decimals = -value.normalize().as_tuple().exponent
    return format_number(value, int(decimals), style)


def format_money(
    value: Decimal, currency: str, minor_units: int, style: str, money_style: str
) -> str:
    """Render an amount with currency: symbol or ISO code, position by locale."""
    number = format_number(value, minor_units, style)
    if money_style == "code":
        return f"{number} {currency}" if style in ("de", "ch") else f"{currency} {number}"
    symbol = CURRENCY_SYMBOLS[currency]
    if currency == "CHF":
        return f"CHF {number}"
    if style in ("de", "ch"):
        return f"{number} {symbol}"
    return f"{symbol}{number}"


def format_rate(rate: Decimal, style: str) -> str:
    """Tax rate without trailing zeros: 19, 8.1 / 8,1, 5.5."""
    text = format(rate.normalize(), "f")
    if "." in text:
        text = text.replace(".", NUMBER_STYLES[style][1])
    return text


def format_date(value: date, fmt: str, months: list[str]) -> str:
    """Format a date in one of the supported printed styles."""
    if fmt == "dmy_dot":
        return value.strftime("%d.%m.%Y")
    if fmt == "iso":
        return value.isoformat()
    if fmt == "mdy_slash":
        return value.strftime("%m/%d/%Y")
    if fmt == "long_en":
        return f"{value.day} {months[value.month - 1]} {value.year}"
    if fmt == "long_de":
        return f"{value.day}. {months[value.month - 1]} {value.year}"
    raise ValueError(fmt)


def group_iban(iban: str) -> str:
    """Printed IBAN: groups of four characters separated by spaces."""
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))
