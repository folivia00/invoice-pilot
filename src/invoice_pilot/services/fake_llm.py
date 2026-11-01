"""Fake model for checks without API keys (``LLM_FAKE=1``): built-in synthetic answers.

It implements the ``Provider`` interface. The three sample files in ``examples/invoices``
get the fields printed on them: ``inv-001`` and ``inv-005`` are auto-approved, and
``inv-007`` has a printed total that is not subtotal + tax, so it needs review. Any
other document gets the ``inv-001`` answer. The answer is chosen by the SHA-256 of what
the provider receives (``DocumentInput.content_sha256``, the key the recordings use):
the extracted text for a text PDF, the file bytes for a scan or a photo. The digests are
built in, so nothing is read from disk at run time.

It never opens a connection, reports zero tokens and costs nothing. It exists for the
quick start, the n8n example and smoke tests; it is off unless ``LLM_FAKE`` is set, and
the processes log a warning at startup when it is on.
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

#: ``examples/invoices/inv-005.jpg``: a photo, JPY without minor units.
INV_005_INVOICE: Final[dict[str, Any]] = {
    "supplier": {
        "name": "Summit Ridge Tools Co., Ltd.",
        "tax_id": None,
        "address": "5-6-2 Midori, 194-4869 Osaka",
    },
    "buyer": {
        "name": "Mara Ellis",
        "tax_id": None,
        "address": "1-2-3 Sakura-cho, 386-8369 Fukuoka",
    },
    "invoice_number": "R029470",
    "invoice_date": "2026-04-09",
    "due_date": None,
    "currency": "JPY",
    "line_items": [
        {"description": "Courier delivery", "quantity": 1, "unit_price": 3610, "amount": 3610},
        {"description": "Monitor 27 inch", "quantity": 10, "unit_price": 38531, "amount": 385310},
        {
            "description": "Training session (half day)",
            "quantity": 1,
            "unit_price": 132109,
            "amount": 132109,
        },
        {"description": "Security audit", "quantity": 1, "unit_price": 214980, "amount": 214980},
    ],
    "subtotal": 736009,
    "tax_rate": 10,
    "tax_amount": 73601,
    "total": 809610,
    "iban": None,
    "bic": None,
}

#: ``examples/invoices/inv-007.pdf``: the printed total 4241.62 is not 3992.58 + 249.54.
INV_007_INVOICE: Final[dict[str, Any]] = {
    "supplier": {
        "name": "Greywater Plumbing LLC",
        "tax_id": "38-6726486",
        "address": "571 Harbor View Lane, Denver 93060",
    },
    "buyer": {
        "name": "Lantern Hill Books Inc.",
        "tax_id": None,
        "address": "522 Harbor View Lane, Tucson 42451",
    },
    "invoice_number": "RE-202694453",
    "invoice_date": "2026-01-03",
    "due_date": "2026-01-10",
    "currency": "USD",
    "line_items": [
        {"description": "Courier delivery", "quantity": 1, "unit_price": 46.00, "amount": 46.00},
        {
            "description": "Printer toner cartridge",
            "quantity": 4,
            "unit_price": 47.53,
            "amount": 190.12,
        },
        {
            "description": "Steel sheet 2 mm, kg",
            "quantity": 46,
            "unit_price": 6.00,
            "amount": 276.00,
        },
        {
            "description": "Onsite installation, hours",
            "quantity": 34,
            "unit_price": 58.51,
            "amount": 1989.34,
        },
        {
            "description": "Maintenance contract Q3",
            "quantity": 1,
            "unit_price": 1491.12,
            "amount": 1491.12,
        },
    ],
    "subtotal": 3992.58,
    "tax_rate": 6.25,
    "tax_amount": 249.54,
    "total": 4241.62,
    "iban": None,
    "bic": None,
}

#: Answer by ``DocumentInput.content_sha256()`` of each sample in ``examples/invoices``.
FAKE_ANSWERS: Final[Mapping[str, str]] = {
    # inv-001.pdf, text PDF: digest of the extracted text
    "34e887aa43980550303c890d07f711ac62a26f9a019bff8d924c7f62e3781c1d": FAKE_ANSWER,
    # inv-005.jpg, photo: digest of the file bytes
    "7d157940415c853342a2fd0eb67cee9529f22d333875bfe6b70d03cabf7de862": json.dumps(INV_005_INVOICE),
    # inv-007.pdf, text PDF: digest of the extracted text
    "a02da770bc5a077cf87d0ee8791c21aa7b6e523d911953de2a229a314bf91e8f": json.dumps(INV_007_INVOICE),
}


def fake_answer(doc: DocumentInput) -> str:
    """The built-in answer for ``doc``: its sample's fields, else ``FAKE_ANSWER``."""
    return FAKE_ANSWERS.get(doc.content_sha256(), FAKE_ANSWER)


class FakeProvider:
    """``Provider`` that answers from ``FAKE_ANSWERS``, with ``FAKE_ANSWER`` as the default."""

    name = FAKE_PROVIDER
    model = FAKE_MODEL

    @property
    def params(self) -> Mapping[str, str]:
        return {}

    async def extract(self, doc: DocumentInput) -> ProviderResponse:
        record = UsageRecord(FAKE_PROVIDER, FAKE_MODEL, "ok", http_status=200)
        return ProviderResponse(raw_text=fake_answer(doc), usage=record, attempts=(record,))


def zero_cost(usage: UsageRecord, on_date: object) -> Decimal:
    """Pricing for the fake model: nothing is billed."""
    return Decimal(0)
