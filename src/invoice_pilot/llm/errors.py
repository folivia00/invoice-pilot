"""Typed failures of the LLM layer.

Messages carry a reason only: never an API key, a request header, a prompt or
any content of the processed document.
"""

from invoice_pilot.llm.types import UsageRecord


class LLMError(Exception):
    """Base class; ``attempts`` lists the usage of every API call made before failing."""

    def __init__(self, message: str, attempts: tuple[UsageRecord, ...] = ()) -> None:
        super().__init__(message)
        self.attempts = attempts


class ProviderError(LLMError):
    """Non-retryable provider failure: 4xx other than 429, or a malformed response envelope."""


class ProviderUnavailableError(LLMError):
    """Retries exhausted: rate limit, 5xx, timeout or network error on every attempt."""


class InvalidResponseError(LLMError):
    """The model answered, but the answer is unusable: not JSON, off-schema, refusal, empty."""


class RecordingMissingError(LLMError):
    """Replay mode found no recording for this document and configuration."""
