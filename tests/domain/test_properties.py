"""Property-based tests (Hypothesis) for the validation rules.

1. An internally consistent invoice is always auto-approved with no errors.
2. Corrupting exactly one amount by more than the tolerance always yields
   needs_review with the expected issue code.
3. Removing one required field always yields needs_review with the
   missing_field code at the right path.
"""

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
from invoice_pilot.domain.constants import quantize_to, tolerance_for
from invoice_pilot.domain.currencies import minor_units
from invoice_pilot.domain.iban import compute_check_digits
from invoice_pilot.domain.validation import REQUIRED_FIELDS, REQUIRED_LINE_ITEM_FIELDS

TODAY = date(2026, 10, 23)
CURRENCIES = ("EUR", "USD", "GBP", "CHF", "JPY", "BHD")  # 2, 2, 2, 2, 0, 3 minor units
IBAN_COUNTRIES = ("DE", "FR", "GB", "AT", "NL", "CH")
TAX_IDS = (None, "DE123456789", "ATU12345678", "FR12345678901", "NL123456789B01", "12-3456789")
TAX_RATES = (
    Decimal("0"),
    Decimal("7"),
    Decimal("8.1"),
    Decimal("19"),
    Decimal("20"),
    Decimal("25"),
)

names = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), max_codepoint=0x24F),
    min_size=1,
    max_size=20,
)
digits = st.text(alphabet="0123456789", min_size=10, max_size=26)


@st.composite
def consistent_invoices(draw: st.DrawFn) -> Invoice:
    """Build an invoice whose amounts, tax, dates and IBAN are consistent by construction."""
    currency = draw(st.sampled_from(CURRENCIES))
    units = minor_units(currency)
    assert units is not None
    unit = Decimal(1).scaleb(-units)
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
    country = draw(st.sampled_from(IBAN_COUNTRIES))
    bban = draw(digits)
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
        iban=country + compute_check_digits(country, bban) + bban,
    )


@given(consistent_invoices())
def test_consistent_invoice_is_always_auto_approved(invoice: Invoice) -> None:
    report = validate(invoice, today=TODAY)
    assert report.errors == []
    assert report.status is ValidationStatus.AUTO_APPROVED


@given(
    consistent_invoices(),
    st.sampled_from(("amount", "subtotal", "tax_amount", "total")),
    st.integers(min_value=0, max_value=10),
    st.integers(min_value=2, max_value=1000),
)
def test_corrupting_one_amount_is_always_caught(
    invoice: Invoice, target: str, item_pick: int, factor: int
) -> None:
    units = minor_units(invoice.currency or "")
    assert units is not None
    delta = tolerance_for(units) * factor  # strictly larger than the tolerance
    if target == "amount":
        index = item_pick % len(invoice.line_items)
        items = list(invoice.line_items)
        item = items[index]
        assert item.amount is not None
        items[index] = item.model_copy(update={"amount": item.amount + delta})
        corrupted = invoice.model_copy(update={"line_items": items})
        expected, path = IssueCode.LINE_AMOUNT_MISMATCH, f"line_items[{index}].amount"
    else:
        current = getattr(invoice, target)
        corrupted = invoice.model_copy(update={target: current + delta})
        expected = {
            "subtotal": IssueCode.SUBTOTAL_MISMATCH,
            "tax_amount": IssueCode.TOTAL_MISMATCH,
            "total": IssueCode.TOTAL_MISMATCH,
        }[target]
        path = "subtotal" if target == "subtotal" else "total"
    report = validate(corrupted, today=TODAY)
    assert report.status is ValidationStatus.NEEDS_REVIEW
    assert report.has(expected, path)


@given(
    consistent_invoices(),
    st.sampled_from(
        REQUIRED_FIELDS + tuple(f"line_items[0].{f}" for f in REQUIRED_LINE_ITEM_FIELDS)
    ),
)
def test_removing_one_required_field_is_always_caught(invoice: Invoice, path: str) -> None:
    if path == "supplier.name":
        broken = invoice.model_copy(
            update={"supplier": invoice.supplier.model_copy(update={"name": None})}
        )
    elif path == "buyer.name":
        broken = invoice.model_copy(
            update={"buyer": invoice.buyer.model_copy(update={"name": None})}
        )
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
