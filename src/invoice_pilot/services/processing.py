"""Upload -> limits -> download -> prepare -> queue -> model -> stored result.

Order (each step only if the previous one passed):
1. type and size from Telegram metadata, before anything is downloaded;
2. a daily slot is reserved (``limits``);
3. the file is downloaded into memory (no file is ever written);
4. ``prepare`` checks the bytes (signature, size, pages) and builds the model input;
5. the document waits for a processing slot (Compact: one at a time) and for the
   rate limiter, which sits inside the provider (``rate_limit``);
6. ``extract_invoice`` runs, the result and every HTTP call are stored, one log line.

Input problems and provider failures give the slot back. The bytes and the
prepared input are dropped as soon as the model call returns.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import PurePosixPath
from typing import Final, Literal

from invoice_pilot.config import TELEGRAM_DOWNLOAD_LIMIT_BYTES
from invoice_pilot.extraction.prepare import InputError, InputLimits, prepare
from invoice_pilot.llm.errors import ProviderError, ProviderUnavailableError
from invoice_pilot.llm.pricing import cost_usd
from invoice_pilot.llm.providers import Provider
from invoice_pilot.llm.types import SUPPORTED_MIMES, UsageRecord
from invoice_pilot.services.extraction import ExtractionOutcome, extract_invoice
from invoice_pilot.services.limits import (
    STATUS_FAILED,
    STATUS_PROVIDER_ERROR,
    STATUS_REJECTED,
    DailyLimits,
)
from invoice_pilot.storage import CallRow, Storage

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]
Monotonic = Callable[[], float]
Notify = Callable[[str], Awaitable[None]]
Download = Callable[[], Awaitable[bytes]]

_EXTENSIONS: Final[dict[str, str]] = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
_MB: Final = 1024 * 1024

MSG_UNSUPPORTED: Final = (
    "Unsupported file. Send an invoice as a PDF, JPEG or PNG file, or as a photo."
)
MSG_LIMIT: Final = "Demo limit reached for today, please try again tomorrow (UTC)."
MSG_BUSY: Final = "The AI service is busy right now. Please try again in a minute."
MSG_DOWNLOAD_FAILED: Final = "Could not download the file from Telegram. Please send it again."
MSG_FAILED: Final = "Something went wrong while processing the file. Please try again later."
QUEUE_TEMPLATE: Final = "In queue (position {position}). I'll reply when it's your turn."


def queue_message(position: int) -> str:
    return QUEUE_TEMPLATE.format(position=position)


@dataclass(frozen=True, slots=True)
class UploadMeta:
    """What Telegram tells about an upload before it is downloaded."""

    kind: Literal["document", "photo"]
    mime_type: str | None
    file_name: str | None
    file_size: int | None


def check_upload(meta: UploadMeta, max_bytes: int) -> str | None:
    """Rejection message for an upload that must not be downloaded, or None."""
    if meta.kind == "document":
        mime = (meta.mime_type or "").lower()
        if mime not in SUPPORTED_MIMES:
            suffix = PurePosixPath(meta.file_name or "").suffix.lower()
            if mime not in ("", "application/octet-stream") or suffix not in _EXTENSIONS:
                return MSG_UNSUPPORTED
    limit = min(max_bytes, TELEGRAM_DOWNLOAD_LIMIT_BYTES)
    if meta.file_size is not None and meta.file_size > limit:
        return (
            f"The file is too large ({meta.file_size / _MB:.1f} MB). "
            f"The limit is {limit / _MB:.0f} MB."
        )
    return None


class ResultKind(Enum):
    DONE = "done"
    REJECTED = "rejected"
    LIMIT = "limit"
    BUSY = "busy"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    """What the bot shows: a message for non-DONE kinds, the outcome for DONE."""

    kind: ResultKind
    message: str | None = None
    outcome: ExtractionOutcome | None = None
    model: str | None = None
    processing_s: float | None = None
    document_id: int | None = None


class ProcessingQueue:
    """At most ``concurrency`` documents at the model; waiters learn their position."""

    def __init__(self, concurrency: int) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self._semaphore = asyncio.Semaphore(concurrency)
        self._waiting = 0
        self.active = 0
        #: Highest number of documents processed at the same time (for tests).
        self.max_active = 0

    @property
    def waiting(self) -> int:
        return self._waiting

    @asynccontextmanager
    async def slot(self, on_wait: Callable[[int], Awaitable[None]]) -> AsyncIterator[None]:
        if self._semaphore.locked() or self._waiting:
            self._waiting += 1
            try:
                position = self._waiting
                try:
                    await on_wait(position)
                except Exception as exc:  # a failed notice must not lose the place in line
                    logger.warning("queue notice not sent: %s", type(exc).__name__)
                await self._semaphore.acquire()
            finally:
                self._waiting -= 1
        else:
            await self._semaphore.acquire()
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            yield
        finally:
            self.active -= 1
            self._semaphore.release()


def _call_rows(records: list[UsageRecord] | tuple[UsageRecord, ...], day: date) -> list[CallRow]:
    return [
        CallRow(
            provider=r.provider,
            model=r.model,
            status=r.status,
            http_status=r.http_status,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            thinking_tokens=r.thinking_tokens,
            latency_ms=r.latency_ms,
            cost_usd=cost_usd(r, day),
        )
        for r in records
    ]


class DocumentProcessor:
    """The service behind the bot's upload handlers."""

    def __init__(
        self,
        *,
        storage: Storage,
        limits: DailyLimits,
        provider: Provider,
        queue: ProcessingQueue,
        input_limits: InputLimits,
        clock: Clock,
        monotonic: Monotonic = time.monotonic,
    ) -> None:
        self._storage = storage
        self._limits = limits
        self._provider = provider
        self._queue = queue
        self._input_limits = input_limits
        self._clock = clock
        self._monotonic = monotonic

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def user_daily_docs(self) -> int:
        return self._limits.user_daily_docs

    async def remaining_today(self, user_id: int) -> int:
        return await self._limits.remaining_today(user_id)

    async def process(
        self, user_id: int, meta: UploadMeta, download: Download, notify: Notify
    ) -> ProcessingResult:
        """Run one upload through the pipeline; never raises for expected failures."""
        rejection = check_upload(meta, self._input_limits.max_bytes)
        if rejection is not None:
            logger.warning("document rejected before download: kind=%s", meta.kind)
            return ProcessingResult(ResultKind.REJECTED, message=rejection)

        reservation = await self._limits.reserve(user_id)
        if reservation is None:
            logger.warning("document refused: daily demo limit reached")
            return ProcessingResult(ResultKind.LIMIT, message=MSG_LIMIT)
        doc_id = reservation.document_id

        try:
            data = await download()
        except Exception as exc:
            await self._limits.release(reservation, STATUS_FAILED, user_id)
            logger.error("document %d: download failed: %s", doc_id, type(exc).__name__)
            return ProcessingResult(ResultKind.FAILED, message=MSG_DOWNLOAD_FAILED)
        try:
            prepared = prepare(data, limits=self._input_limits)
        except InputError as exc:
            del data
            await self._limits.release(reservation, STATUS_REJECTED, user_id)
            logger.warning("document %d rejected: %s", doc_id, type(exc).__name__)
            return ProcessingResult(ResultKind.REJECTED, message=_input_message(exc))

        input_kind = prepared.input_kind
        try:
            async with self._queue.slot(lambda position: notify(queue_message(position))):
                started = self._monotonic()
                outcome = await extract_invoice(
                    data, provider=self._provider, today=self._clock().date(), prepared=prepared
                )
                elapsed_ms = int((self._monotonic() - started) * 1000)
        except (ProviderUnavailableError, ProviderError) as exc:
            del data, prepared
            await self._store_provider_failure(doc_id, input_kind, list(exc.attempts))
            statuses = [r.http_status for r in exc.attempts]
            logger.error(
                "document %d failed: input_kind=%s model=%s error=%s http_statuses=%s",
                doc_id,
                input_kind,
                self._provider.model,
                type(exc).__name__,
                statuses,
            )
            return ProcessingResult(ResultKind.BUSY, message=MSG_BUSY, document_id=doc_id)
        except Exception as exc:
            del data, prepared
            await self._limits.release(reservation, STATUS_FAILED, user_id)
            logger.error("document %d failed unexpectedly: %s", doc_id, type(exc).__name__)
            return ProcessingResult(ResultKind.FAILED, message=MSG_FAILED, document_id=doc_id)
        del data, prepared

        cost = await self._store_outcome(doc_id, outcome, elapsed_ms)
        logger.info(
            "document processed: id=%d input_kind=%s status=%s issues=%d model=%s "
            "tokens_in=%d tokens_out=%d latency_ms=%d cost_usd=%s",
            doc_id,
            outcome.input_kind,
            outcome.report.status.value,
            len(outcome.report.issues),
            self._provider.model,
            outcome.input_tokens,
            outcome.output_tokens,
            elapsed_ms,
            cost,
        )
        return ProcessingResult(
            ResultKind.DONE,
            outcome=outcome,
            model=self._provider.model,
            processing_s=elapsed_ms / 1000,
            document_id=doc_id,
        )

    async def _store_outcome(
        self, doc_id: int, outcome: ExtractionOutcome, elapsed_ms: int
    ) -> Decimal:
        now = self._clock()
        rows = _call_rows(outcome.usage, now.date())
        issues = [
            {
                "code": issue.code.value,
                "field": issue.field,
                "severity": issue.severity.value,
                "message": issue.message,
            }
            for issue in outcome.report.issues
        ]
        invoice = outcome.invoice.model_dump(mode="json") if outcome.invoice else None
        async with self._storage.transaction() as repo:
            await repo.finish_document(
                doc_id,
                status=outcome.report.status.value,
                input_kind=outcome.input_kind,
                pages=outcome.pages,
                issues=issues,
                invoice=invoice,
                model=self._provider.model,
                attempts=outcome.attempts,
                processing_ms=elapsed_ms,
            )
            await repo.add_llm_calls(doc_id, now, rows)
        return sum((row.cost_usd for row in rows), Decimal(0))

    async def _store_provider_failure(
        self, doc_id: int, input_kind: str, records: list[UsageRecord]
    ) -> None:
        now = self._clock()
        async with self._storage.transaction() as repo:
            await repo.finish_document(
                doc_id,
                status=STATUS_PROVIDER_ERROR,
                input_kind=input_kind,
                pages=None,
                issues=None,
                invoice=None,
                model=self._provider.model,
                attempts=0,
                processing_ms=None,
            )
            await repo.add_llm_calls(doc_id, now, _call_rows(records, now.date()))


def _input_message(exc: InputError) -> str:
    text = str(exc)
    return f"Cannot process this file: {text}." if text else MSG_UNSUPPORTED
