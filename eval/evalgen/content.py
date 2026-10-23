"""Content generation: a domain Invoice plus everything needed to print it.

Calculation conventions (the domain validation assumes exactly these):
    amount   = round_half_up(quantity x unit_price) to the currency minor units
    subtotal = sum(amount)                                   (exact)
    tax      = round_half_up(subtotal x rate / 100)          (minor units)
    total    = subtotal + tax                                (exact)
One tax rate per invoice. Corruptions change the printed value (and therefore
the ground truth, which is "as printed") after the clean invoice is built.
"""

import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from evalgen import REFERENCE_DATE
from evalgen.formats import (
    format_date,
    format_money,
    format_number,
    format_quantity,
    format_rate,
    group_iban,
)
from evalgen.identifiers import (
    IBAN_COUNTRIES,
    VAT_COUNTRIES,
    corrupt_iban_check_digits,
    digits,
    make_bad_vat_id,
    make_bic,
    make_ein,
    make_iban,
    make_vat_id,
)
from evalgen.plan import DocPlan
from evalgen.pricing import (
    TOTAL_EUR_MAX,
    TOTAL_EUR_MIN,
    eur_equivalent,
    quantity,
    unit_price,
)
from invoice_pilot.domain import Invoice, LineItem, Party
from invoice_pilot.domain.constants import quantize_to
from invoice_pilot.domain.currencies import minor_units

LOCALES_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "dataset" / "locales"

CURRENCY_BY_COUNTRY: Final[dict[str, str]] = {
    "DE": "EUR",
    "AT": "EUR",
    "NL": "EUR",
    "FR": "EUR",
    "IT": "EUR",
    "ES": "EUR",
    "PL": "EUR",
    "BE": "EUR",
    "GB": "GBP",
    "US": "USD",
    "CH": "CHF",
    "JP": "JPY",
}

#: Realistic tax rates per country: (standard, reduced, zero) with weights 70/20/10.
TAX_RATES: Final[dict[str, tuple[str, ...]]] = {
    "DE": ("19", "7", "0"),
    "AT": ("20", "10", "0"),
    "CH": ("8.1", "2.6", "0"),
    "GB": ("20", "5", "0"),
    "NL": ("21", "9", "0"),
    "FR": ("20", "10", "5.5", "0"),
    "IT": ("22", "10", "0"),
    "ES": ("21", "10", "0"),
    "PL": ("23", "8", "0"),
    "BE": ("21", "6", "0"),
    "US": ("6.25", "8.875", "7", "0"),
    "JP": ("10", "8"),
}

#: Postal code shapes per country ('#' = digit, 'A' = letter).
POSTAL_FORMATS: Final[dict[str, str]] = {
    "DE": "#####",
    "AT": "####",
    "CH": "####",
    "GB": "AA# #AA",
    "US": "#####",
    "NL": "#### AA",
    "FR": "#####",
    "IT": "#####",
    "ES": "#####",
    "PL": "##-###",
    "BE": "####",
    "JP": "###-####",
}

#: Supplier country weights per document language.
COUNTRY_WEIGHTS: Final[dict[str, dict[str, int]]] = {
    "de": {"DE": 60, "AT": 25, "CH": 15},
    "en": {"GB": 28, "US": 22, "NL": 10, "FR": 10, "IT": 8, "ES": 8, "PL": 7, "BE": 7},
}

#: Primary issue codes expected for each corruption kind.
EXPECTED_CODES: Final[dict[str, tuple[str, ...]]] = {
    "total_mismatch": ("total_mismatch",),
    "line_amount_mismatch": ("line_amount_mismatch",),
    "subtotal_mismatch": ("subtotal_mismatch",),
    "missing_invoice_number": ("missing_field",),
    "bad_iban_check_digits": ("invalid_iban",),
    "due_before_invoice_date": ("due_before_invoice_date",),
    "bad_vat_format": ("invalid_tax_id",),
}

#: Item sets are redrawn from the same random stream until the invoice total,
#: in EUR equivalent, lies inside [TOTAL_EUR_MIN; TOTAL_EUR_MAX] with a margin
#: that leaves room for the money corruptions (at most 50 minor units).
MAX_ITEM_ATTEMPTS: Final[int] = 50
TOTAL_MARGIN_EUR: Final[Decimal] = Decimal(1)

PAGE_A4: Final[tuple[float, float]] = (595.28, 841.89)
PAGE_LETTER: Final[tuple[float, float]] = (612.0, 792.0)


@dataclass(frozen=True, slots=True)
class PrintedItem:
    description: str
    quantity: str
    unit_price: str
    amount: str
    unit: str = ""  # printed unit label (pcs / h / kg / m), never part of the model
    unit_type: str = "piece"


@dataclass(slots=True)
class DocContent:
    """Everything the layout needs, plus the ground truth."""

    plan: DocPlan
    invoice: Invoice
    labels: dict[str, str]
    supplier_country: str
    buyer_country: str
    currency: str
    minor_units: int
    number_style: str
    date_format: str
    money_style: str
    page_size: tuple[float, float]
    supplier_lines: list[str]
    buyer_lines: list[str]
    items: list[PrintedItem]
    printed: dict[str, str]
    tax_label: str
    payment_terms: str
    customer_number: str
    contact: dict[str, str]
    expected_status: str
    expected_issue_codes: list[str]
    extra: dict[str, Any] = field(default_factory=dict)


def load_locales() -> dict[str, dict[str, Any]]:
    """Load en.json and de.json from eval/dataset/locales."""
    return {
        lang: json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        for lang in ("en", "de")
    }


def _weighted(rng: random.Random, weights: dict[str, int]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()), k=1)[0]


def _postal(rng: random.Random, country: str) -> str:
    return "".join(
        rng.choice("0123456789")
        if ch == "#"
        else rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ")
        if ch == "A"
        else ch
        for ch in POSTAL_FORMATS[country]
    )


def _address(rng: random.Random, locale: dict[str, Any], country: str) -> list[str]:
    streets = locale["streets"].get(country) or locale["streets"][next(iter(locale["streets"]))]
    cities = locale["cities"].get(country) or locale["cities"][next(iter(locale["cities"]))]
    name = rng.choice(streets)
    if country == "JP":
        street = name  # locale entries already carry the block numbers
    elif country in ("US", "GB"):
        street = f"{rng.randint(1, 980)} {name}"
    else:
        street = f"{name} {rng.randint(1, 180)}"
    city_line = f"{_postal(rng, country)} {rng.choice(cities)}"
    if country in ("US", "GB"):
        city_line = f"{rng.choice(cities)} {_postal(rng, country)}"
    return [street, city_line]


def _company(rng: random.Random, locale: dict[str, Any], country: str) -> str:
    return (
        f"{rng.choice(locale['company_names'])} {rng.choice(locale['company_suffixes'][country])}"
    )


def _person(rng: random.Random, locale: dict[str, Any]) -> str:
    return f"{rng.choice(locale['first_names'])} {rng.choice(locale['last_names'])}"


def _pick_supplier_country(rng: random.Random, plan: DocPlan) -> str:
    if plan.jpy:
        return "JP"
    weights = dict(COUNTRY_WEIGHTS[plan.language])
    if plan.corruption == "bad_vat_format":
        weights = {c: w for c, w in weights.items() if c in VAT_COUNTRIES}
    if plan.corruption == "bad_iban_check_digits":
        weights = {c: w for c, w in weights.items() if c in IBAN_COUNTRIES}
    return _weighted(rng, weights)


def _invoice_number(rng: random.Random, year: int) -> str:
    n = rng.randint(1, 99999)
    return rng.choice(
        [
            f"INV-{year}-{n:04d}",
            f"RE-{year}{n:05d}",
            f"{year}-{n:06d}",
            f"R{n:06d}",
            f"{year % 100}{rng.randint(1, 12):02d}-{n:04d}",
        ]
    )


def _date_format(rng: random.Random, language: str, currency: str) -> str:
    if language == "de":
        return _weighted(rng, {"dmy_dot": 50, "long_de": 30, "iso": 20})
    if currency == "USD":
        return _weighted(rng, {"mdy_slash": 50, "iso": 25, "long_en": 25})
    return _weighted(rng, {"iso": 40, "long_en": 35, "dmy_dot": 25})


def build_content(plan: DocPlan, seed: int, locales: dict[str, dict[str, Any]]) -> DocContent:
    """Build the full content of one document deterministically from (seed, plan)."""
    rng = random.Random(seed * 1000 + plan.index)
    locale = locales[plan.language]
    labels: dict[str, str] = dict(locale["labels"])
    country = _pick_supplier_country(rng, plan)
    currency = CURRENCY_BY_COUNTRY[country]
    units = minor_units(currency)
    assert units is not None
    number_style = "ch" if country == "CH" else ("de" if plan.language == "de" else "en")
    date_format = _date_format(rng, plan.language, currency)
    money_style = "symbol" if rng.random() < 0.6 else "code"
    page_size = PAGE_LETTER if country == "US" else PAGE_A4
    reference = date.fromisoformat(REFERENCE_DATE)

    # --- parties -----------------------------------------------------------------
    supplier_name = _company(rng, locale, country)
    supplier_addr = _address(rng, locale, country)
    buyer_country = (
        country
        if rng.random() < 0.75
        else rng.choice([c for c in COUNTRY_WEIGHTS[plan.language] if c != country] or [country])
    )
    buyer_name = (
        _company(rng, locale, buyer_country) if rng.random() < 0.7 else _person(rng, locale)
    )
    buyer_addr = _address(rng, locale, buyer_country) if rng.random() < 0.8 else []
    supplier_tax_id: str | None = None
    if country in VAT_COUNTRIES and (rng.random() < 0.85 or plan.corruption == "bad_vat_format"):
        supplier_tax_id = make_vat_id(rng, country)
    elif country == "US" and rng.random() < 0.6:
        supplier_tax_id = make_ein(rng)
    buyer_tax_id: str | None = None
    if buyer_country in VAT_COUNTRIES and buyer_name.endswith(
        tuple(locale["company_suffixes"][buyer_country])
    ):
        if rng.random() < 0.5:
            buyer_tax_id = make_vat_id(rng, buyer_country)
    country_names = locale["countries"]
    supplier_lines = [supplier_name, *supplier_addr]
    if buyer_country != country or rng.random() < 0.4:
        supplier_lines.append(country_names[country])
    buyer_lines = [buyer_name, *buyer_addr]
    if buyer_addr and buyer_country != country:
        buyer_lines.append(country_names[buyer_country])

    # --- dates, number ---------------------------------------------------------------
    invoice_date = reference - timedelta(days=rng.randint(0, 364))
    due_days = rng.choice((7, 14, 14, 30, 30, 45))
    due_date: date | None = invoice_date + timedelta(days=due_days) if rng.random() < 0.75 else None
    invoice_number: str | None = _invoice_number(rng, invoice_date.year)

    # --- items and sums (conventions, see module docstring) ---------------------------
    count = rng.randint(6, 8) if plan.two_pages else rng.randint(1, 8)
    rates = TAX_RATES[country]
    rate = Decimal(_weighted(rng, {r: w for r, w in zip(rates, (70, 20, 10, 10), strict=False)}))
    attempts = 0
    while True:
        attempts += 1
        catalog = rng.sample(locale["items"], count)
        items = [_line_item(rng, entry, currency, units) for entry in catalog]
        subtotal = sum((item.amount for item in items if item.amount is not None), Decimal(0))
        tax_amount = quantize_to(subtotal * rate / Decimal(100), units)
        total = subtotal + tax_amount
        total_eur = eur_equivalent(total, currency)
        if TOTAL_EUR_MIN + TOTAL_MARGIN_EUR <= total_eur <= TOTAL_EUR_MAX - TOTAL_MARGIN_EUR:
            break
        if attempts >= MAX_ITEM_ATTEMPTS:
            raise RuntimeError(f"{plan.doc_id}: no item set inside the total range")
    unit_types = [entry["unit"] for entry in catalog]

    # --- bank ----------------------------------------------------------------------------
    iban: str | None = None
    bic: str | None = None
    if country in IBAN_COUNTRIES and (
        rng.random() < 0.8 or plan.corruption == "bad_iban_check_digits"
    ):
        iban = make_iban(rng, country)
        if rng.random() < 0.5:
            bic = make_bic(rng, country)

    # --- corruption: printed values change, ground truth follows -------------------------
    expected_status = "auto_approved"
    expected_codes: list[str] = []
    kind = plan.corruption
    unit = Decimal(1).scaleb(-units)
    if kind:
        expected_status = "needs_review"
        expected_codes = list(EXPECTED_CODES[kind])
        delta = unit * rng.choice((1, 2, 5, 10, 50)) * rng.choice((1, -1))
        if kind == "total_mismatch":
            total = max(total + delta, unit)
        elif kind == "line_amount_mismatch":
            index = rng.randrange(len(items))
            item = items[index]
            assert item.amount is not None
            items[index] = item.model_copy(update={"amount": max(item.amount + delta, unit)})
        elif kind == "subtotal_mismatch":
            subtotal = max(subtotal + delta, unit)
        elif kind == "missing_invoice_number":
            invoice_number = None
        elif kind == "bad_iban_check_digits":
            assert iban is not None
            iban = corrupt_iban_check_digits(rng, iban)
        elif kind == "due_before_invoice_date":
            due_date = invoice_date - timedelta(days=rng.randint(3, 30))
        elif kind == "bad_vat_format":
            supplier_tax_id = make_bad_vat_id(rng, country)

    invoice = Invoice(
        supplier=Party(
            name=supplier_name, tax_id=supplier_tax_id, address=", ".join(supplier_addr)
        ),
        buyer=Party(
            name=buyer_name,
            tax_id=buyer_tax_id,
            address=", ".join(buyer_addr) if buyer_addr else None,
        ),
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        due_date=due_date,
        currency=currency,
        line_items=items,
        subtotal=subtotal,
        tax_rate=rate,
        tax_amount=tax_amount,
        total=total,
        iban=iban,
        bic=bic,
    )

    # --- printed strings --------------------------------------------------------------------
    months = locale["months"]
    printed_items = [
        PrintedItem(
            description=item.description or "",
            quantity=format_quantity(item.quantity or Decimal(0), number_style),
            unit_price=format_number(
                item.unit_price or Decimal(0), _price_decimals(item, units), number_style
            ),
            amount=format_number(item.amount or Decimal(0), units, number_style),
            unit=locale["units"][unit_type],
            unit_type=unit_type,
        )
        for item, unit_type in zip(items, unit_types, strict=True)
    ]
    tax_label_key = "tax_us" if country == "US" else "tax"
    tax_label = labels[tax_label_key].replace("{rate}", format_rate(rate, number_style))
    printed = {
        "invoice_number": invoice_number or "",
        "invoice_date": format_date(invoice_date, date_format, months),
        "due_date": format_date(due_date, date_format, months) if due_date else "",
        "subtotal": format_money(subtotal, currency, units, number_style, money_style),
        "tax_amount": format_money(tax_amount, currency, units, number_style, money_style),
        "total": format_money(total, currency, units, number_style, money_style),
        "iban": group_iban(iban) if iban else "",
        "bic": bic or "",
        "supplier_tax_id": supplier_tax_id or "",
        "buyer_tax_id": buyer_tax_id or "",
        "currency": currency,
    }
    payment_terms = (
        labels["payment_terms"].replace("{days}", str(due_days))
        if due_date and kind != "due_before_invoice_date"
        else labels["payment_terms_no_due"]
    )
    domain = "".join(ch for ch in supplier_name.split()[0].lower() if ch.isascii() and ch.isalnum())
    contact = {
        "phone": f"+{rng.randint(30, 49)} {rng.randint(10, 99)} {digits(rng, 3)} {digits(rng, 4)}",
        "email": f"billing@{domain or 'invoice'}.example",
    }
    return DocContent(
        plan=plan,
        invoice=invoice,
        labels=labels,
        supplier_country=country,
        buyer_country=buyer_country,
        currency=currency,
        minor_units=units,
        number_style=number_style,
        date_format=date_format,
        money_style=money_style,
        page_size=page_size,
        supplier_lines=supplier_lines,
        buyer_lines=buyer_lines,
        items=printed_items,
        printed=printed,
        tax_label=tax_label,
        payment_terms=payment_terms,
        customer_number=f"K-{digits(rng, 5)}" if plan.language == "de" else f"C-{digits(rng, 5)}",
        contact=contact,
        expected_status=expected_status,
        expected_issue_codes=expected_codes,
        extra={
            "tax_id_label": labels["tax_id_us"] if country == "US" else labels["tax_id"],
            "item_attempts": attempts,
        },
    )


def _line_item(rng: random.Random, entry: dict[str, Any], currency: str, units: int) -> LineItem:
    """One line item priced from the catalog entry's EUR range scaled to ``currency``."""
    qty = quantity(rng, entry)
    price = unit_price(rng, entry, currency, units)
    amount = quantize_to(qty * price, units)
    assert amount > 0, entry
    return LineItem(description=entry["text"], quantity=qty, unit_price=price, amount=amount)


def _price_decimals(item: LineItem, units: int) -> int:
    """Print unit prices with at least the currency decimals, more if the value has them."""
    assert item.unit_price is not None
    exponent = -item.unit_price.as_tuple().exponent
    return max(units, int(exponent))


def build_all(seed: int, plans: list[DocPlan]) -> list[DocContent]:
    """Build the content of every planned document."""
    locales = load_locales()
    return [build_content(plan, seed, locales) for plan in plans]


def ground_truth_payload(content: DocContent) -> dict[str, Any]:
    """Ground-truth JSON without file-dependent fields (file, sha256, pages)."""
    plan = content.plan
    return {
        "id": plan.doc_id,
        "template": plan.template,
        "language": plan.language,
        "input_type": plan.input_type,
        "currency": content.currency,
        "supplier_country": content.supplier_country,
        "number_format": content.number_style,
        "date_format": content.date_format,
        "corruption": plan.corruption,
        "expected_status": content.expected_status,
        "expected_issue_codes": content.expected_issue_codes,
        "invoice": content.invoice.model_dump(mode="json"),
        "printed": content.printed,
        "line_units": [item.unit_type for item in content.items],
    }
