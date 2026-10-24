"""LLM layer: response schema, prompt, providers behind one interface, usage accounting.

Nothing here touches the network on import. Providers receive an injected
``httpx.AsyncClient``; tests use ``httpx.MockTransport`` and never call a real API.
"""

from invoice_pilot.llm.errors import (
    InvalidResponseError,
    LLMError,
    ProviderError,
    ProviderUnavailableError,
    RecordingMissingError,
)
from invoice_pilot.llm.types import DocumentInput, ProviderResponse, UsageRecord

__all__ = [
    "DocumentInput",
    "InvalidResponseError",
    "LLMError",
    "ProviderError",
    "ProviderResponse",
    "ProviderUnavailableError",
    "RecordingMissingError",
    "UsageRecord",
]
