"""Model response text -> domain ``Invoice``.

The response is validated with ``model_validate_json`` on the raw string, so
JSON numbers become ``Decimal`` exactly (never through ``float``). Every field
of the schema is required here too; a missing or unknown key, a non-ISO date or
a non-numeric amount is an ``InvalidResponseError`` whose message names the
field and the error type only, never the offending value.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, ValidationError

from invoice_pilot.domain import Invoice
from invoice_pilot.llm.errors import InvalidResponseError

_STRICT = ConfigDict(extra="forbid", frozen=True)


class PartyDTO(BaseModel):
    model_config = _STRICT

    name: str | None
    tax_id: str | None
    address: str | None


class LineItemDTO(BaseModel):
    model_config = _STRICT

    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None


class InvoiceDTO(BaseModel):
    """Exact shape of the JSON the model must return (see ``llm.schema``)."""

    model_config = _STRICT

    supplier: PartyDTO
    buyer: PartyDTO
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    currency: str | None
    line_items: list[LineItemDTO]
    subtotal: Decimal | None
    tax_rate: Decimal | None
    tax_amount: Decimal | None
    total: Decimal | None
    iban: str | None
    bic: str | None


def parse_invoice(raw_text: str) -> Invoice:
    """Validate the model's JSON text and convert it to the domain model."""
    try:
        dto = InvoiceDTO.model_validate_json(raw_text)
    except ValidationError as exc:
        raise InvalidResponseError(_describe(exc)) from None
    try:
        return Invoice.model_validate(dto.model_dump())
    except ValidationError as exc:  # pragma: no cover - DTO and domain model agree by design
        raise InvalidResponseError(_describe(exc)) from None


def _describe(exc: ValidationError, limit: int = 5) -> str:
    """Field paths and error types only (no input values)."""
    parts = []
    for error in exc.errors(include_input=False, include_url=False)[:limit]:
        path = ".".join(str(piece) for piece in error["loc"]) or "$"
        parts.append(f"{path}: {error['type']}")
    more = exc.error_count() - limit
    suffix = f" (+{more} more)" if more > 0 else ""
    return "model response does not match the schema: " + "; ".join(parts) + suffix
