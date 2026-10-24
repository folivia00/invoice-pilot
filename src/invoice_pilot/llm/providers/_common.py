"""Helpers shared by the concrete providers."""

import base64
from collections.abc import Sequence
from typing import Any

import httpx

from invoice_pilot.llm.errors import ProviderError
from invoice_pilot.llm.types import DocumentInput, UsageRecord


def encode_base64(doc: DocumentInput) -> str:
    """Base64 of the document bytes, computed once per extraction (not per retry)."""
    assert doc.data is not None
    return base64.b64encode(doc.data).decode("ascii")


def as_int(value: object) -> int:
    """Token counter from a JSON envelope; anything but a non-bool int counts as 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def json_envelope(
    response: httpx.Response, provider: str, attempts: Sequence[UsageRecord]
) -> dict[str, Any]:
    """The response body as a JSON object, or ProviderError."""
    try:
        payload = response.json()
    except ValueError:
        raise ProviderError(f"{provider}: response is not JSON", tuple(attempts)) from None
    if not isinstance(payload, dict):
        raise ProviderError(f"{provider}: response envelope is not an object", tuple(attempts))
    return payload


def dict_field(
    value: object, what: str, provider: str, attempts: Sequence[UsageRecord]
) -> dict[str, Any]:
    """``value`` as an object ({} when absent), or ProviderError when it is something else."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProviderError(f"{provider}: {what} is not an object", tuple(attempts))
    return value


def list_of_dicts(
    value: object, what: str, provider: str, attempts: Sequence[UsageRecord]
) -> list[dict[str, Any]]:
    """``value`` as a list of objects ([] when absent), or ProviderError."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ProviderError(f"{provider}: {what} is not a list of objects", tuple(attempts))
    return value
