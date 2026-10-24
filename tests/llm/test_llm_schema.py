"""The response schema stays inside the subset both providers accept."""

import json
from pathlib import Path
from typing import Any

from invoice_pilot.llm.schema import (
    ALLOWED_KEYWORDS,
    INVOICE_SCHEMA,
    SCHEMA_NAME,
    schema_json,
    schema_sha256,
)

SNAPSHOT = Path(__file__).parent / "snapshots" / "invoice_schema.json"

FORBIDDEN = {
    "$ref",
    "$defs",
    "definitions",
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "pattern",
    "format",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "nullable",
    "default",
    "propertyOrdering",
}


def _nodes(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Every schema node (root, properties, array items), depth first."""
    found = [schema]
    for child in schema.get("properties", {}).values():
        found.extend(_nodes(child))
    if "items" in schema:
        found.extend(_nodes(schema["items"]))
    return found


def test_schema_matches_snapshot() -> None:
    assert json.loads(SNAPSHOT.read_text(encoding="utf-8")) == INVOICE_SCHEMA


def test_schema_uses_only_allowed_keywords() -> None:
    for node in _nodes(INVOICE_SCHEMA):
        assert set(node) <= ALLOWED_KEYWORDS, set(node) - ALLOWED_KEYWORDS
        assert not (set(node) & FORBIDDEN)
        kind = node["type"]
        if isinstance(kind, list):
            assert len(kind) == 2 and kind[1] == "null" and kind[0] != "null"
        else:
            assert kind in ("object", "array")
        assert node["description"]


def test_objects_are_closed_and_fully_required() -> None:
    objects = [n for n in _nodes(INVOICE_SCHEMA) if n["type"] == "object"]
    assert len(objects) == 4  # invoice, supplier, buyer, line item
    for node in objects:
        assert node["additionalProperties"] is False
        assert node["required"] == list(node["properties"])
        assert len(set(node["required"])) == len(node["required"])


def test_root_shape_mirrors_domain_invoice() -> None:
    assert INVOICE_SCHEMA["type"] == "object"
    assert list(INVOICE_SCHEMA["properties"]) == [
        "supplier",
        "buyer",
        "invoice_number",
        "invoice_date",
        "due_date",
        "currency",
        "line_items",
        "subtotal",
        "tax_rate",
        "tax_amount",
        "total",
        "iban",
        "bic",
    ]
    for key in ("subtotal", "tax_rate", "tax_amount", "total"):
        assert INVOICE_SCHEMA["properties"][key]["type"] == ["number", "null"]
    for key in ("invoice_date", "due_date", "currency", "iban", "bic", "invoice_number"):
        assert INVOICE_SCHEMA["properties"][key]["type"] == ["string", "null"]
    line = INVOICE_SCHEMA["properties"]["line_items"]
    assert line["type"] == "array" and list(line["items"]["properties"]) == [
        "description",
        "quantity",
        "unit_price",
        "amount",
    ]
    assert SCHEMA_NAME.replace("_", "").isalnum()


def test_schema_digest_is_canonical() -> None:
    assert schema_json() == json.dumps(INVOICE_SCHEMA, sort_keys=True, separators=(",", ":"))
    digest = schema_sha256()
    assert len(digest) == 64 and digest == schema_sha256()
