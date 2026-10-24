"""One test per normalization rule of docs/evaluation.md."""

from datetime import date
from decimal import Decimal
from typing import Any

from evalrun.compare import KEY_FIELDS, TEXT_FIELDS, all_key_fields_correct, compare_invoices
from invoice_pilot.domain import Invoice

BASE: dict[str, Any] = {
    "supplier": {"name": "Acme Ltd", "tax_id": "GB123456789", "address": "1 High St, Leeds"},
    "buyer": {"name": "Bob Builder", "tax_id": "DE123456789", "address": "Main Road 2\nBerlin"},
    "invoice_number": "INV-2026-001",
    "invoice_date": "2026-09-01",
    "due_date": "2026-09-15",
    "currency": "GBP",
    "line_items": [
        {"description": "Consulting, hours", "quantity": "2", "unit_price": "100", "amount": "200"},
        {"description": "Courier delivery", "quantity": "1", "unit_price": "40", "amount": "40"},
    ],
    "subtotal": "240",
    "tax_rate": "20",
    "tax_amount": "48",
    "total": "288",
    "iban": "GB29NWBK60161331926819",
    "bic": "NWBKGB2L",
}


def _invoice(**changes: Any) -> Invoice:
    payload = {**BASE, **changes}
    return Invoice.model_validate(payload)


def _wrong(expected: Invoice, actual: Invoice) -> set[str]:
    fields = compare_invoices(expected, actual)
    return {name for name, ok in fields.items() if not ok}


def test_identical_invoices_match_everywhere() -> None:
    fields = compare_invoices(_invoice(), _invoice())
    assert set(fields) == set(KEY_FIELDS) | set(TEXT_FIELDS)
    assert all(fields.values()) and all_key_fields_correct(fields)


def test_invoice_number_whitespace_is_collapsed() -> None:
    assert _wrong(_invoice(), _invoice(invoice_number="  INV-2026-001 ")) == set()
    assert _wrong(_invoice(invoice_number="A  B"), _invoice(invoice_number="A B")) == set()
    assert _wrong(_invoice(), _invoice(invoice_number="INV-2026-002")) == {"invoice_number"}


def test_dates_compare_as_calendar_dates() -> None:
    assert _wrong(_invoice(), _invoice(due_date=date(2026, 9, 15))) == set()
    assert _wrong(_invoice(), _invoice(due_date="2026-09-16")) == {"due_date"}


def test_currency_is_case_insensitive() -> None:
    assert _wrong(_invoice(), _invoice(currency="gbp ")) == set()
    assert _wrong(_invoice(), _invoice(currency="EUR")) == {"currency"}


def test_amounts_are_numeric_decimals() -> None:
    assert _wrong(_invoice(tax_rate="19.9"), _invoice(tax_rate="19.90")) == set()
    assert _wrong(_invoice(), _invoice(total="288.00", subtotal=Decimal("240.0"))) == set()
    assert _wrong(_invoice(), _invoice(total="288.01")) == {"total"}
    assert _wrong(_invoice(), _invoice(tax_amount="48.1")) == {"tax_amount"}


def test_iban_ignores_spaces_and_case() -> None:
    assert _wrong(_invoice(), _invoice(iban="gb29 nwbk 6016 1331 9268 19")) == set()
    assert _wrong(_invoice(), _invoice(iban="GB29NWBK60161331926810")) == {"iban"}


def test_tax_ids_use_domain_normalization() -> None:
    assert (
        _wrong(_invoice(), _invoice(supplier={**BASE["supplier"], "tax_id": "gb 123 456 789"}))
        == set()
    )
    assert (
        _wrong(_invoice(), _invoice(buyer={**BASE["buyer"], "tax_id": "DE-123.456.789"})) == set()
    )
    assert _wrong(_invoice(), _invoice(buyer={**BASE["buyer"], "tax_id": "DE123456780"})) == {
        "buyer.tax_id"
    }


def test_bic_ignores_spaces_and_case() -> None:
    assert _wrong(_invoice(), _invoice(bic="nwbk gb2l")) == set()
    assert _wrong(_invoice(), _invoice(bic="NWBKGB2LXXX")) == {"bic"}


def test_names_ignore_case_and_whitespace() -> None:
    assert (
        _wrong(_invoice(), _invoice(supplier={**BASE["supplier"], "name": " ACME   ltd "})) == set()
    )
    assert _wrong(_invoice(), _invoice(buyer={**BASE["buyer"], "name": "Bob Builders"})) == {
        "buyer.name"
    }


def test_line_items_need_same_count_order_and_numbers() -> None:
    reordered = _invoice(line_items=list(reversed(BASE["line_items"])))
    assert _wrong(_invoice(), reordered) == {"line_items", "line_items.description"}
    fewer = _invoice(line_items=BASE["line_items"][:1])
    assert _wrong(_invoice(), fewer) == {"line_items", "line_items.description"}
    same_text = [
        {**BASE["line_items"][0], "description": "CONSULTING HOURS"},
        BASE["line_items"][1],
    ]
    assert _wrong(_invoice(), _invoice(line_items=same_text)) == set()  # commas and case ignored
    renamed = [{**BASE["line_items"][0], "description": "Consulting work"}, BASE["line_items"][1]]
    assert _wrong(_invoice(), _invoice(line_items=renamed)) == {"line_items.description"}
    scaled = [
        {**BASE["line_items"][0], "quantity": "2.0", "amount": "200.00"},
        BASE["line_items"][1],
    ]
    assert _wrong(_invoice(), _invoice(line_items=scaled)) == set()


def test_free_text_normalization() -> None:
    buyer = {**BASE["buyer"], "address": "main road 2, berlin"}
    assert _wrong(_invoice(), _invoice(buyer=buyer)) == set()
    supplier = {**BASE["supplier"], "address": "1 High St\nLeeds"}
    assert _wrong(_invoice(), _invoice(supplier=supplier)) == set()
    supplier = {**BASE["supplier"], "address": "2 High St, Leeds"}
    assert _wrong(_invoice(), _invoice(supplier=supplier)) == {"supplier.address"}


def test_null_handling() -> None:
    assert _wrong(_invoice(bic=None), _invoice(bic=None)) == set()
    assert _wrong(_invoice(bic=None), _invoice()) == {"bic"}
    assert _wrong(_invoice(), _invoice(bic=None)) == {"bic"}
    assert _wrong(_invoice(due_date=None), _invoice(due_date=None)) == set()


def test_missing_extraction_fails_every_field() -> None:
    fields = compare_invoices(_invoice(), None)
    assert not any(fields.values()) and not all_key_fields_correct(fields)
    assert set(fields) == set(KEY_FIELDS) | set(TEXT_FIELDS)
