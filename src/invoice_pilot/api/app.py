"""HTTP API (aiohttp.web): ``GET /health`` and ``POST /extract``.

``/extract`` runs the bot's pipeline (daily limits, queue, rate limiter, model,
validation, storage) for one multipart upload. Checks before the body is read:
the bearer token (constant-time comparison), then ``Content-Length`` against the
profile file limit. The file part is read in chunks into memory and the read
stops at the limit, so an oversized file is never read whole; it is never
written to disk (``request.post()`` would spool file parts to a temporary file).

All API documents belong to one pseudo-user, ``API_USER_ID``: the API counts
its own daily documents (``API_DAILY_DOCS``) in its own database, apart from
Telegram users (whose IDs are positive). Logs: one INFO line per ``/extract``
request with metadata only (no token, no client address, no content).
"""

import hmac
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Final

from aiohttp import BodyPartReader, web

from invoice_pilot.extraction.prepare import detect_mime
from invoice_pilot.services.processing import (
    MSG_QUEUE_FULL,
    DocumentProcessor,
    ProcessingResult,
    ResultKind,
    UploadMeta,
)

logger = logging.getLogger(__name__)

#: Owner of every document uploaded through the API (never a Telegram user ID).
API_USER_ID: Final = 0
FILE_FIELD: Final = "file"
RETRY_AFTER_SECONDS: Final = 60
#: Room for the multipart boundaries and part headers on top of the file limit.
MULTIPART_OVERHEAD_BYTES: Final = 64 * 1024
CHUNK_BYTES: Final = 64 * 1024

PROCESSOR_KEY: Final = web.AppKey("processor", DocumentProcessor)
TOKEN_KEY: Final = web.AppKey("api_token", str)
MAX_FILE_KEY: Final = web.AppKey("max_file_bytes", int)

Monotonic = Callable[[], float]
MONOTONIC_KEY: Final = web.AppKey("monotonic", Monotonic)


def error_response(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> web.Response:
    return web.json_response(
        {"error": {"code": code, "message": message}}, status=status, headers=headers
    )


class FileTooLarge(Exception):
    pass


def _authorized(request: web.Request) -> bool:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    return hmac.compare_digest(token.strip().encode(), request.app[TOKEN_KEY].encode())


async def _read_file(request: web.Request, limit: int) -> tuple[bytes, str | None]:
    """The ``file`` part's bytes and file name; raise ``FileTooLarge`` or ``LookupError``."""
    reader = await request.multipart()
    while True:
        part = await reader.next()
        if part is None:
            raise LookupError(FILE_FIELD)
        if not isinstance(part, BodyPartReader) or part.name != FILE_FIELD:
            await part.release()
            continue
        buffer = bytearray()
        while chunk := await part.read_chunk(CHUNK_BYTES):
            buffer += chunk
            if len(buffer) > limit:
                raise FileTooLarge
        return bytes(buffer), part.filename


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def extract(request: web.Request) -> web.Response:
    started = request.app[MONOTONIC_KEY]()
    response, result = await _extract(request)
    outcome = result.outcome if result is not None else None
    logger.info(
        "api extract: http_status=%d input_kind=%s status=%s latency_ms=%d cost_usd=%s",
        response.status,
        outcome.input_kind if outcome else "-",
        outcome.report.status.value if outcome else "-",
        round((request.app[MONOTONIC_KEY]() - started) * 1000),
        result.cost_usd if result is not None and result.cost_usd is not None else "-",
    )
    return response


async def _extract(request: web.Request) -> tuple[web.Response, ProcessingResult | None]:
    if not _authorized(request):
        return error_response(
            401,
            "unauthorized",
            "missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        ), None
    limit = request.app[MAX_FILE_KEY]
    too_large = error_response(
        413, "file_too_large", f"the file limit is {limit // (1024 * 1024)} MB"
    )
    if (request.content_length or 0) > limit + MULTIPART_OVERHEAD_BYTES:
        return too_large, None
    if request.content_type != "multipart/form-data":
        return error_response(
            400, "missing_file", f"send multipart/form-data with a {FILE_FIELD!r} field"
        ), None
    try:
        data, file_name = await _read_file(request, limit)
    except FileTooLarge:
        return too_large, None
    except LookupError:
        return error_response(400, "missing_file", f"no {FILE_FIELD!r} field in the form"), None
    if not data:
        return error_response(400, "missing_file", "the file is empty"), None
    mime = detect_mime(data)  # the file signature decides, not the declared Content-Type
    if mime is None:
        return error_response(
            400, "unsupported_file", "unsupported file type: send a PDF, JPEG or PNG file"
        ), None
    meta = UploadMeta(kind="document", mime_type=mime, file_name=file_name, file_size=len(data))
    result = await request.app[PROCESSOR_KEY].process(
        API_USER_ID, meta, _hand_over(data), _no_notice
    )
    del data
    return _response(result), result


def _hand_over(data: bytes) -> Callable[[], Awaitable[bytes]]:
    async def download() -> bytes:
        return data

    return download


async def _no_notice(text: str) -> None:
    return None


def _response(result: ProcessingResult) -> web.Response:
    retry = {"Retry-After": str(RETRY_AFTER_SECONDS)}
    if result.kind is ResultKind.DONE and result.outcome is not None:
        return web.json_response(_body(result))
    if result.kind is ResultKind.REJECTED:
        return error_response(400, "invalid_file", result.message or "cannot process this file")
    if result.kind is ResultKind.LIMIT:
        return error_response(429, "daily_limit", "daily document limit reached (UTC day)")
    if result.kind is ResultKind.BUSY:
        if result.message == MSG_QUEUE_FULL:
            return error_response(
                503, "queue_full", "too many documents in line, try again later", retry
            )
        return error_response(
            503, "provider_unavailable", "the model API is unavailable, try again later", retry
        )
    return error_response(500, "internal_error", "the document could not be processed")


def _body(result: ProcessingResult) -> dict[str, Any]:
    outcome = result.outcome
    assert outcome is not None
    return {
        "document_id": result.document_id,
        "status": outcome.report.status.value,
        "issues": [
            {
                "code": issue.code.value,
                "field": issue.field,
                "severity": issue.severity.value,
                "message": issue.message,
            }
            for issue in outcome.report.issues
        ],
        "invoice": outcome.invoice.model_dump(mode="json") if outcome.invoice else None,
        "input_kind": outcome.input_kind,
        "pages": outcome.pages,
        "model": result.model,
        "processing_ms": round((result.processing_s or 0) * 1000),
    }


def build_app(
    processor: DocumentProcessor,
    *,
    api_token: str,
    max_file_bytes: int,
    monotonic: Monotonic = time.monotonic,
) -> web.Application:
    """The aiohttp application; ``api_token`` must not be empty."""
    if not api_token:
        raise ValueError("API_TOKEN must not be empty")
    app = web.Application(client_max_size=max_file_bytes + MULTIPART_OVERHEAD_BYTES)
    app[PROCESSOR_KEY] = processor
    app[TOKEN_KEY] = api_token
    app[MAX_FILE_KEY] = max_file_bytes
    app[MONOTONIC_KEY] = monotonic
    app.router.add_get("/health", health)
    app.router.add_post("/extract", extract)
    return app
