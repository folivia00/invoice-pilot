"""Value types shared by providers, the recording wrapper and the extraction service."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Final, Literal

CallStatus = Literal["ok", "invalid_response", "provider_error", "timeout"]
InputKind = Literal["text_pdf", "scan_pdf", "photo"]

MIME_PDF: Final = "application/pdf"
MIME_JPEG: Final = "image/jpeg"
MIME_PNG: Final = "image/png"
SUPPORTED_MIMES: Final[frozenset[str]] = frozenset({MIME_PDF, MIME_JPEG, MIME_PNG})


@dataclass(frozen=True, slots=True)
class DocumentInput:
    """What is sent to the model: extracted text, or the raw file bytes with their MIME type.

    Exactly one of ``text`` and ``data`` is set. ``data`` references the caller's
    bytes object (no copy). The fields are excluded from ``repr`` so a logged
    exception can never carry document content.
    """

    text: str | None = field(default=None, repr=False)
    data: bytes | None = field(default=None, repr=False)
    mime: str | None = None

    def __post_init__(self) -> None:
        if (self.text is None) == (self.data is None):
            raise ValueError("DocumentInput needs exactly one of text or data")
        if self.data is not None and self.mime not in SUPPORTED_MIMES:
            raise ValueError(f"unsupported MIME type {self.mime!r}")
        if self.text is not None and self.mime is not None:
            raise ValueError("text input has no MIME type")

    @property
    def is_text(self) -> bool:
        return self.text is not None

    def content_sha256(self) -> str:
        """Digest of the document content (used in recording keys)."""
        payload = self.text.encode("utf-8") if self.text is not None else self.data
        assert payload is not None
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """Actual consumption of one API call, successful or not.

    ``output_tokens`` includes thinking/reasoning tokens (both providers bill them
    as output); ``thinking_tokens`` repeats that part for reports.
    """

    provider: str
    model: str
    status: CallStatus
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    latency_ms: int = 0
    http_status: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """Raw model text plus the usage of the call that produced it.

    ``attempts`` lists every HTTP attempt of this extraction, failed retries
    first; its last element is ``usage``.
    """

    raw_text: str
    usage: UsageRecord
    attempts: tuple[UsageRecord, ...]


def mark_invalid(records: Sequence[UsageRecord]) -> tuple[UsageRecord, ...]:
    """Copy of ``records`` with the last (HTTP-successful) call marked ``invalid_response``."""
    if not records:
        return ()
    return (*records[:-1], replace(records[-1], status="invalid_response"))
