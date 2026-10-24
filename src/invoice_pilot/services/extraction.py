"""Document bytes -> validated invoice: prepare, call the model, parse, validate.

A model answer that is not usable (refusal, non-JSON, off-schema) is requested
once more; a second failure yields ``invoice=None`` and a ``needs_review`` report
with the ``extraction_failed`` issue. Provider failures propagate to the caller.
Logs carry metadata only.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Final

from invoice_pilot.domain import (
    Invoice,
    Issue,
    IssueCode,
    Severity,
    ValidationReport,
    ValidationStatus,
    validate,
)
from invoice_pilot.extraction.prepare import InputLimits, PreparedDocument, prepare
from invoice_pilot.llm.errors import InvalidResponseError, LLMError
from invoice_pilot.llm.parse import parse_invoice
from invoice_pilot.llm.providers import Provider
from invoice_pilot.llm.types import InputKind, UsageRecord, mark_invalid

logger = logging.getLogger(__name__)

#: One retry after an unusable model answer.
MAX_MODEL_ATTEMPTS: Final = 2

UsageSink = Callable[[UsageRecord], None]


@dataclass(slots=True)
class ExtractionOutcome:
    invoice: Invoice | None
    report: ValidationReport
    input_kind: InputKind
    pages: int
    attempts: int
    usage: list[UsageRecord] = field(default_factory=list)

    @property
    def input_tokens(self) -> int:
        return sum(record.input_tokens for record in self.usage)

    @property
    def output_tokens(self) -> int:
        return sum(record.output_tokens for record in self.usage)


async def extract_invoice(
    file_bytes: bytes,
    *,
    provider: Provider,
    today: date,
    limits: InputLimits | None = None,
    on_usage: UsageSink | None = None,
    prepared: PreparedDocument | None = None,
) -> ExtractionOutcome:
    """Run the full pipeline on one uploaded file.

    Raises ``InputError`` (unsupported, too large, too many pages) before any
    model call, and ``ProviderUnavailableError`` / ``ProviderError`` when the
    provider fails; both are the caller's to present. A caller that already ran
    ``prepare`` on the bytes passes the result as ``prepared`` to avoid a second pass.
    """
    if prepared is None:
        prepared = prepare(file_bytes, limits=limits)
    doc = prepared.input
    records: list[UsageRecord] = []

    def emit(new: tuple[UsageRecord, ...] | list[UsageRecord]) -> None:
        for record in new:
            records.append(record)
            if on_usage is not None:
                on_usage(record)

    failure: InvalidResponseError | None = None
    for attempt in range(1, MAX_MODEL_ATTEMPTS + 1):
        try:
            response = await provider.extract(doc)
        except InvalidResponseError as exc:
            emit(exc.attempts)
            failure = exc
            logger.warning(
                "extraction attempt %d: unusable answer (input_kind=%s model=%s): %s",
                attempt,
                prepared.input_kind,
                provider.model,
                exc,
            )
            continue
        except LLMError as exc:
            emit(exc.attempts)
            logger.error(
                "extraction failed (input_kind=%s model=%s): %s: %s",
                prepared.input_kind,
                provider.model,
                type(exc).__name__,
                exc,
            )
            raise
        try:
            invoice = parse_invoice(response.raw_text)
        except InvalidResponseError as exc:
            emit(mark_invalid(response.attempts))
            failure = exc
            logger.warning(
                "extraction attempt %d: off-schema answer (input_kind=%s model=%s): %s",
                attempt,
                prepared.input_kind,
                provider.model,
                exc,
            )
            continue
        emit(response.attempts)
        report = validate(invoice, today=today)
        _log_done(prepared.input_kind, provider.model, report.status, attempt, records)
        return ExtractionOutcome(
            invoice=invoice,
            report=report,
            input_kind=prepared.input_kind,
            pages=prepared.pages,
            attempts=attempt,
            usage=records,
        )

    report = ValidationReport(
        status=ValidationStatus.NEEDS_REVIEW,
        issues=[
            Issue(
                code=IssueCode.EXTRACTION_FAILED,
                field="document",
                severity=Severity.ERROR,
                message=f"the model returned no usable invoice after {MAX_MODEL_ATTEMPTS} "
                f"attempts: {failure}",
            )
        ],
    )
    _log_done(prepared.input_kind, provider.model, report.status, MAX_MODEL_ATTEMPTS, records)
    return ExtractionOutcome(
        invoice=None,
        report=report,
        input_kind=prepared.input_kind,
        pages=prepared.pages,
        attempts=MAX_MODEL_ATTEMPTS,
        usage=records,
    )


def _log_done(
    input_kind: str, model: str, status: ValidationStatus, attempts: int, records: list[UsageRecord]
) -> None:
    logger.info(
        "extraction done: input_kind=%s model=%s status=%s attempts=%d calls=%d "
        "input_tokens=%d output_tokens=%d latency_ms=%d",
        input_kind,
        model,
        status.value,
        attempts,
        len(records),
        sum(r.input_tokens for r in records),
        sum(r.output_tokens for r in records),
        sum(r.latency_ms for r in records),
    )
