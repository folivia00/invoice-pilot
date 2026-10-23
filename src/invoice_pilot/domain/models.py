"""Domain model of an invoice as extracted by the LLM.

Every extracted field is optional: a missing value is a validation finding,
not a parsing error. Money, quantities and rates are ``Decimal``. pydantic 2.13
converts both JSON numbers (``19.99``) and JSON strings (``"19.99"``) to
``Decimal("19.99")`` exactly, without float artifacts (covered by tests).

The models are frozen: validation never mutates an invoice.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

_CONFIG = ConfigDict(frozen=True, extra="forbid")


class Party(BaseModel):
    """Supplier or buyer."""

    model_config = _CONFIG

    name: str | None = None
    tax_id: str | None = None
    address: str | None = None


class LineItem(BaseModel):
    """One invoice line."""

    model_config = _CONFIG

    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class Invoice(BaseModel):
    """Invoice fields. One tax rate per invoice (mixed rates are out of scope in v1)."""

    model_config = _CONFIG

    supplier: Party = Field(default_factory=Party)
    buyer: Party = Field(default_factory=Party)
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Decimal | None = None
    #: Percentage, e.g. Decimal("19") for 19 %.
    tax_rate: Decimal | None = None
    tax_amount: Decimal | None = None
    total: Decimal | None = None
    iban: str | None = None
    bic: str | None = None
