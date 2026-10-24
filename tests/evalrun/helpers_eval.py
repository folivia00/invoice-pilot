"""Helpers shared by the evaluation runner tests (tests/ is not a package)."""

import json
import re
from decimal import Decimal
from typing import Any

NUMERIC_KEYS = {"quantity", "unit_price", "amount", "subtotal", "tax_rate", "tax_amount", "total"}
RECORDED_AT = "2026-10-25T09:30:00+00:00"


def answer_json(invoice: dict[str, Any]) -> str:
    """Ground-truth invoice dict (Decimal strings) -> model answer with JSON numbers."""

    def convert(node: Any, key: str | None = None) -> Any:
        if isinstance(node, dict):
            return {k: convert(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [convert(v, key) for v in node]
        if key in NUMERIC_KEYS and isinstance(node, str):
            return Decimal(node)
        return node

    text = json.dumps(convert(invoice), default=lambda d: f"@@RAW:{d}@@")
    return re.sub(r'"@@RAW:([^"]*)@@"', r"\1", text)
