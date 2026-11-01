"""Fake model for checks without API keys (``LLM_FAKE=1``): one built-in synthetic answer.

It implements the ``Provider`` interface and answers every document with the same
synthetic invoice (consistent totals, so it is auto-approved), whatever the input.
It never opens a connection, reports zero tokens and costs nothing. It exists for
the quick start, the n8n example and smoke tests; it is off unless ``LLM_FAKE`` is
set, and the processes log a warning at startup when it is on.
"""

import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Final

from invoice_pilot.llm.types import DocumentInput, ProviderResponse, UsageRecord

FAKE_PROVIDER: Final = "fake"
FAKE_MODEL: Final = "fake-invoice-v1"

#: Synthetic invoice in the exact shape the model must return (``llm.parse.InvoiceDTO``).
FAKE_INVOICE: Final[dict[str, Any]] = {
    "supplier": {
        "name": "Northwind Supplies Corp.",
        "tax_id": None,
        "address": "565 Elm Court, Raleigh 78792",
    },
    "buyer": {
        "name": "Orbit Office Solutions LLC",
        "tax_id": None,
        "address": "244 Industrial Parkway, Portland 56295",
    },
    "invoice_number": "2025-053582",
    "invoice_date": "2025-11-27",
    "due_date": "2025-12-11",
    "currency": "USD",
    "line_items": [
        {
            "description": "Onsite installation, hours",
            "quantity": 3,
            "unit_price": 97.35,
            "amount": 292.05,
        },
        {
            "description": "Packaging boxes, set of 50",
            "quantity": 8,
            "unit_price": 40.00,
            "amount": 320.00,
        },
        {
            "description": "Vinyl banner print, m",
            "quantity": 5.7,
            "unit_price": 20.54,
            "amount": 117.08,
        },
        {
            "description": "Office chair, ergonomic",
            "quantity": 8,
            "unit_price": 355.73,
            "amount": 2845.84,
        },
    ],
    "subtotal": 3574.97,
    "tax_rate": 6.25,
    "tax_amount": 223.44,
    "total": 3798.41,
    "iban": None,
    "bic": None,
}
FAKE_ANSWER: Final = json.dumps(FAKE_INVOICE)


class FakeProvider:
    """``Provider`` that returns ``FAKE_ANSWER`` for every document."""

    name = FAKE_PROVIDER
    model = FAKE_MODEL

    @property
    def params(self) -> Mapping[str, str]:
        return {}

    async def extract(self, doc: DocumentInput) -> ProviderResponse:
        record = UsageRecord(FAKE_PROVIDER, FAKE_MODEL, "ok", http_status=200)
        return ProviderResponse(raw_text=FAKE_ANSWER, usage=record, attempts=(record,))


def zero_cost(usage: UsageRecord, on_date: object) -> Decimal:
    """Pricing for the fake model: nothing is billed."""
    return Decimal(0)
