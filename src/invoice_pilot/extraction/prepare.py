"""Turn uploaded bytes into a ``DocumentInput`` without decoding images.

Steps: detect the type from the file signature, enforce size and page limits,
then for PDFs decide between the text layer (sent as text) and the raw bytes
(scan, sent as a document). Images are never opened; they go to the provider
as-is. ``pypdf`` is imported lazily inside the PDF branch only.
"""

import io
from dataclasses import dataclass
from typing import Final

from invoice_pilot.llm.types import MIME_JPEG, MIME_PDF, MIME_PNG, DocumentInput, InputKind

#: Compact profile defaults (CLAUDE.md): 5 MB and 5 pages.
DEFAULT_MAX_BYTES: Final = 5 * 1024 * 1024
DEFAULT_MAX_PAGES: Final = 5

#: A PDF counts as "text" when its pages yield at least this many letters and
#: digits in total. Any real invoice prints far more (names, addresses, numbers,
#: dates, totals); scanned PDFs yield none; a stray watermark or page stamp on a
#: scan stays below the threshold and is still sent as a document.
TEXT_LAYER_MIN_CHARS: Final = 200

_SIGNATURES: Final[tuple[tuple[bytes, str], ...]] = (
    (b"%PDF-", MIME_PDF),
    (b"\xff\xd8\xff", MIME_JPEG),
    (b"\x89PNG\r\n\x1a\n", MIME_PNG),
)


class InputError(ValueError):
    """Problem with the uploaded file; the message is safe to show to the user."""


class UnsupportedFileError(InputError):
    pass


class FileTooLargeError(InputError):
    pass


class TooManyPagesError(InputError):
    pass


class UnreadablePdfError(InputError):
    pass


@dataclass(frozen=True, slots=True)
class InputLimits:
    max_bytes: int = DEFAULT_MAX_BYTES
    max_pages: int = DEFAULT_MAX_PAGES


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    input: DocumentInput
    input_kind: InputKind
    mime: str
    pages: int
    size_bytes: int


def detect_mime(data: bytes) -> str | None:
    """MIME type from the leading bytes, or None if the type is not supported."""
    for signature, mime in _SIGNATURES:
        if data[: len(signature)] == signature:
            return mime
    return None


def prepare(data: bytes, *, limits: InputLimits | None = None) -> PreparedDocument:
    """Validate ``data`` against ``limits`` and build the provider input."""
    limits = limits or InputLimits()
    mime = detect_mime(data)
    if mime is None:
        raise UnsupportedFileError("unsupported file type: send a PDF, JPEG or PNG file")
    if len(data) > limits.max_bytes:
        raise FileTooLargeError(
            f"file is too large: {len(data) / 1_048_576:.1f} MB, "
            f"the limit is {limits.max_bytes / 1_048_576:.0f} MB"
        )
    if mime != MIME_PDF:
        return PreparedDocument(
            input=DocumentInput(data=data, mime=mime),
            input_kind="photo",
            mime=mime,
            pages=1,
            size_bytes=len(data),
        )
    pages, text = _pdf_pages_and_text(data, limits.max_pages)
    if _text_chars(text) >= TEXT_LAYER_MIN_CHARS:
        return PreparedDocument(
            input=DocumentInput(text=text),
            input_kind="text_pdf",
            mime=mime,
            pages=pages,
            size_bytes=len(data),
        )
    return PreparedDocument(
        input=DocumentInput(data=data, mime=mime),
        input_kind="scan_pdf",
        mime=mime,
        pages=pages,
        size_bytes=len(data),
    )


def _pdf_pages_and_text(data: bytes, max_pages: int) -> tuple[int, str]:
    from pypdf import PdfReader  # lazy: the bot process must not pay for pypdf at import

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise UnreadablePdfError("the PDF is password protected")
        pages = len(reader.pages)
    except InputError:
        raise
    except Exception as exc:
        raise UnreadablePdfError("the PDF cannot be read") from exc
    if pages > max_pages:
        raise TooManyPagesError(f"the PDF has {pages} pages, the limit is {max_pages}")
    try:
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise UnreadablePdfError("the PDF cannot be read") from exc
    return pages, text


def _text_chars(text: str) -> int:
    return sum(1 for ch in text if ch.isalnum())
