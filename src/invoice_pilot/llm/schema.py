"""JSON Schema of the model response, shared by both providers.

Written out explicitly (not generated from pydantic) so it stays inside the
subset both structured-output implementations accept: ``type`` (including the
``["x", "null"]`` form), ``properties``, ``required``, ``items``, ``enum``,
``additionalProperties: false`` and ``description``. No ``$ref``/``$defs``,
``anyOf``, ``format``, ``pattern`` or length constraints. Every property is
required (OpenAI strict mode); optional values are expressed with ``null``.
The structure mirrors ``invoice_pilot.domain.Invoice``.
"""

import hashlib
import json
from typing import Any, Final

#: Name sent to OpenAI as ``text.format.name``.
SCHEMA_NAME: Final = "invoice_extraction"

#: Keywords the schema may use (checked by tests).
ALLOWED_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"type", "properties", "required", "items", "enum", "additionalProperties", "description"}
)

_DATE = "ISO 8601 date (YYYY-MM-DD) as printed, or null."
_MONEY = "Amount as printed, as a number with a dot decimal separator; null if not printed."


def _nullable(kind: str, description: str) -> dict[str, Any]:
    return {"type": [kind, "null"], "description": description}


def _object(description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _party(description: str) -> dict[str, Any]:
    return _object(
        description,
        {
            "name": _nullable("string", "Company or person name as printed."),
            "tax_id": _nullable(
                "string", "VAT ID, EIN or other tax identifier exactly as printed."
            ),
            "address": _nullable("string", "Full postal address on one line, as printed."),
        },
    )


INVOICE_SCHEMA: Final[dict[str, Any]] = _object(
    "Fields of one invoice, copied exactly as printed.",
    {
        "supplier": _party("Party that issued the invoice (seller, sender)."),
        "buyer": _party("Party the invoice is addressed to (customer, bill to)."),
        "invoice_number": _nullable("string", "Invoice identifier as printed."),
        "invoice_date": _nullable("string", _DATE),
        "due_date": _nullable("string", _DATE),
        "currency": _nullable("string", "ISO 4217 currency code, e.g. EUR, USD, GBP, CHF, JPY."),
        "line_items": {
            "type": "array",
            "description": "Printed line items in document order; empty if none.",
            "items": _object(
                "One printed line.",
                {
                    "description": _nullable("string", "Line text as printed."),
                    "quantity": _nullable("number", "Quantity as printed; null if not printed."),
                    "unit_price": _nullable("number", _MONEY),
                    "amount": _nullable("number", _MONEY),
                },
            ),
        },
        "subtotal": _nullable("number", _MONEY),
        "tax_rate": _nullable("number", "Tax rate in percent as printed, e.g. 19 for 19 %."),
        "tax_amount": _nullable("number", _MONEY),
        "total": _nullable("number", _MONEY),
        "iban": _nullable("string", "IBAN exactly as printed, including spaces."),
        "bic": _nullable("string", "BIC/SWIFT code exactly as printed."),
    },
)


def schema_json() -> str:
    """Canonical serialization (sorted keys, compact) used for hashing."""
    return json.dumps(INVOICE_SCHEMA, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def schema_sha256() -> str:
    """Digest of the canonical schema; part of every recording key."""
    return hashlib.sha256(schema_json().encode("ascii")).hexdigest()
