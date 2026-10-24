"""Helpers shared by the concrete providers."""

import base64

from invoice_pilot.llm.types import DocumentInput


def encode_base64(doc: DocumentInput) -> str:
    """Base64 of the document bytes, computed once per extraction (not per retry)."""
    assert doc.data is not None
    return base64.b64encode(doc.data).decode("ascii")


def as_int(value: object) -> int:
    """Token counter from a JSON envelope; anything but a non-bool int counts as 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
