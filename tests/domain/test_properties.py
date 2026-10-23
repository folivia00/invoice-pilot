"""Property-based tests (Hypothesis) for the validation rules.

1. An internally consistent invoice is always auto-approved with no errors.
2. Corrupting exactly one amount by one minor unit (up or down) or by a larger
   multiple always yields needs_review with the expected issue code.
3. Removing one required field always yields needs_review with the
   missing_field code at the right path.
"""

import re
import string
from datetime import date, timedelta
from decimal import Decimal

from hypothesis import assume, given
from hypothesis import strategies as st

from invoice_pilot.domain import (
    Invoice,
    IssueCode,
    LineItem,
    Party,
    ValidationStatus,
    validate,
)
from invoice_pilot.domain.constants import minor_unit, quantize_to
from invoice_pilot.domain.currencies import minor_units
from invoice_pilot.domain.iban import IBAN_REGISTRY, compute_check_digits
from invoice_pilot.domain.validation import REQUIRED_FIELDS, REQUIRED_LINE_ITEM_FIELDS

TODAY = date(2026, 10, 23)
CURRENCIES = ("EUR", "USD", "GBP", "CHF", "JPY", "BHD")  # 2, 2, 2, 2, 0, 3 minor units
IBAN_COUNTRIES = ("DE", "FR", "GB", "AT", "NL", "CH", "IT", "ES", "PL", "BE")
TAX_IDS = (None, "DE123456789", "ATU12345678", "FR12345678901", "NL123456789B01", "12-3456789")
TAX_RATES = (
    Decimal("0"),
    Decimal("7"),
    Decimal("8.1"),
    Decimal("19"),
    Decimal("20"),
    Decimal("25"),
)
AMOUNT_TARGETS = ("amount", "subtotal", "tax_amount", "total")

names = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), max_codepoint=0x24F),
    min_size=1,
    max_size=20,
)
_STRUCTURE_TOKEN = re.compile(r"(\d+)!([nac])")
_ALPHABETS = {
    "n": string.digits,
    "a": string.ascii_uppercase,
    "c": string.ascii_uppercase + string.digits,
}


@st.composite
def registry_ibans(draw: st.DrawFn, countries: tuple[str, ...] = IBAN_COUNTRIES) -> str:
    """Build an IBAN that follows the registry structure of a country, with valid check digits."""
    country = draw(st.sampled_from(countries))
    tokens = _STRUCTURE_TOKEN.findall(IBAN_REGISTRY[country].structure[2:])
    # tokens[0] is the check-digit field (2!n); the rest describe the BBAN.
    bban = "".join(
        draw(st.text(alphabet=_ALPHABETS[kind], min_size=int(n), max_size=int(n)))
        for n, kind in tokens[1:]
    )
    return country + compute_check_digits(country, bban) + bban


@st.composite
def consistent_invoices(draw: st.DrawFn) -> Invoice:
    """Build an invoice that is consistent by construction.

    Calculation conventions (the validation rules assume exactly these):

    * ``amount   = round_half_up(quantity x unit_price)`` to the minor units of
      the currency (rule d allows half a unit, the maximum rounding error);
    * ``subtotal = sum(amount)`` exactly (rule e is exact);
    * ``tax      = round_half_up(subtotal x rate / 100)`` to the minor units
      (rule g allows half a unit);
    * ``total    = subtotal + tax`` exactly (rule f is exact).

    Quantities have up to 3 decimals, unit prices are quantized to the currency.
    Line amounts that round to zero are rejected with ``assume`` (rule l would
    flag a zero total). The IBAN follows the registry structure of its country
    with valid check digits; ``invoice_date <= TODAY`` and ``due_date >=
    invoice_date``; tax IDs are valid samples or None.
    """
    currency = draw(st.sampled_from(CURRENCIES))
    units = minor_units(currency)
    assert units is not None
    unit = minor_unit(units)
    items: list[LineItem] = []
    for _ in range(draw(st.integers(min_value=1, max_value=6))):
        quantity = draw(
            st.decimals(min_value=Decimal("0.001"), max_value=Decimal("1000"), places=3)
        )
        unit_price = draw(st.decimals(min_value=unit, max_value=Decimal("10000"), places=units))
        amount = quantize_to(quantity * unit_price, units)
        assume(amount > 0)
        items.append(
            LineItem(
                description=draw(names), quantity=quantity, unit_price=unit_price, amount=amount
            )
        )
    subtotal = sum((item.amount for item in items if item.amount is not None), Decimal(0))
    tax_rate = draw(st.sampled_from(TAX_RATES))
    tax_amount = quantize_to(subtotal * tax_rate / Decimal(100), units)
    invoice_date = draw(st.dates(min_value=date(2020, 1, 1), max_value=TODAY))
    due_date = invoice_date + timedelta(days=draw(st.integers(min_value=0, max_value=90)))
    return Invoice(
        supplier=Party(name=draw(names), tax_id=draw(st.sampled_from(TAX_IDS))),
        buyer=Party(name=draw(names), tax_id=draw(st.sampled_from(TAX_IDS))),
        invoice_number=draw(names),
        invoice_date=invoice_date,
        due_date=due_date,
        currency=currency,
        line_items=items,
        subtotal=subtotal,
        tax_rate=tax_rate,
        tax_amount=tax_amount,
        total=subtotal + tax_amount,
        iban=draw(registry_ibans()),
    )


def _corrupt(
    invoice: Invoice, target: str, item_pick: int, delta: Decimal
) -> tuple[Invoice, IssueCode, str] | None:
    """Add ``delta`` to one amount; return (invoice, expected code, field) or None if negative."""
    if target == "amount":
        index = item_pick % len(invoice.line_items)
        items = list(invoice.line_items)
        item = items[index]
        assert item.amount is not None
        if item.amount + delta < 0:
            return None
        items[index] = item.model_copy(update={"amount": item.amount + delta})
        # Rule d may or may not fire (half-unit tolerance); rule e (exact) always does.
        return (
            invoice.model_copy(update={"line_items": items}),
            IssueCode.SUBTOTAL_MISMATCH,
            "subtotal",
        )
    current = getattr(invoice, target)
    if current + delta < 0:
        return None
    corrupted = invoice.model_copy(update={target: current + delta})
    expected = {
        "subtotal": IssueCode.SUBTOTAL_MISMATCH,
        "tax_amount": IssueCode.TOTAL_MISMATCH,
        "total": IssueCode.TOTAL_MISMATCH,
    }[target]
    return corrupted, expected, ("subtotal" if target == "subtotal" else "total")


@given(consistent_invoices())
def test_consistent_invoice_is_always_auto_approved(invoice: Invoice) -> None:
    report = validate(invoice, today=TODAY)
    assert report.errors == []
    assert report.status is ValidationStatus.AUTO_APPROVED


@given(
    consistent_invoices(),
    st.sampled_from(AMOUNT_TARGETS),
    st.integers(min_value=0, max_value=10),
    st.sampled_from((-1, 1)),
)
def test_one_minor_unit_error_is_always_caught(
    invoice: Invoice, target: str, item_pick: int, direction: int
) -> None:
    units = minor_units(invoice.currency or "")
    assert units is not None
    result = _corrupt(invoice, target, item_pick, minor_unit(units) * direction)
    assume(result is not None)
    assert result is not None
    corrupted, expected, path = result
    report = validate(corrupted, today=TODAY)
    assert report.status is ValidationStatus.NEEDS_REVIEW
    assert report.has(expected, path)


@given(
    consistent_invoices(),
    st.sampled_from(AMOUNT_TARGETS),
    st.integers(min_value=0, max_value=10),
    st.integers(min_value=2, max_value=1000),
)
def test_larger_amount_error_is_always_caught(
    invoice: Invoice, target: str, item_pick: int, factor: int
) -> None:
    units = minor_units(invoice.currency or "")
    assert units is not None
    result = _corrupt(invoice, target, item_pick, minor_unit(units) * factor)
    assert result is not None  # positive delta never goes negative
    corrupted, expected, path = result
    report = validate(corrupted, today=TODAY)
    assert report.status is ValidationStatus.NEEDS_REVIEW
    assert report.has(expected, path)
    if target == "amount":
        # Two or more units always exceed the half-unit rounding tolerance of rule d.
        assert report.has(IssueCode.LINE_AMOUNT_MISMATCH)


@given(
    consistent_invoices(),
    st.sampled_from(
        REQUIRED_FIELDS + tuple(f"line_items[0].{f}" for f in REQUIRED_LINE_ITEM_FIELDS)
    ),
)
def test_removing_one_required_field_is_always_caught(invoice: Invoice, path: str) -> None:
    if path == "supplier.name":
        supplier = invoice.supplier.model_copy(update={"name": None})
        broken = invoice.model_copy(update={"supplier": supplier})
    elif path == "buyer.name":
        buyer = invoice.buyer.model_copy(update={"name": None})
        broken = invoice.model_copy(update={"buyer": buyer})
    elif path == "line_items":
        broken = invoice.model_copy(update={"line_items": []})
    elif path.startswith("line_items[0]."):
        field = path.split(".", 1)[1]
        items = list(invoice.line_items)
        items[0] = items[0].model_copy(update={field: None})
        broken = invoice.model_copy(update={"line_items": items})
    else:
        broken = invoice.model_copy(update={path: None})
    report = validate(broken, today=TODAY)
    assert report.status is ValidationStatus.NEEDS_REVIEW
    assert report.has(IssueCode.MISSING_FIELD, path)
