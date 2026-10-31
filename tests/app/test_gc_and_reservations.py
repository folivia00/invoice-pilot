"""Garbage collection after every document, stale reservations at startup, no user on refusal."""

import asyncio
import logging
from decimal import Decimal
from pathlib import Path

import pytest
from helpers_app import (
    FakeClock,
    FakeProvider,
    dataset_bytes,
    make_processor,
    open_storage,
    truth_answer,
)

from invoice_pilot.llm.errors import ProviderUnavailableError
from invoice_pilot.llm.types import UsageRecord
from invoice_pilot.services.limits import (
    RESERVED_COST_PER_DOCUMENT_USD,
    DailyLimits,
    fail_stale_reservations,
)
from invoice_pilot.services.processing import (
    DocumentProcessor,
    ProcessingResult,
    ResultKind,
    UploadMeta,
)
from invoice_pilot.storage import Storage

PDF = dataset_bytes("inv-001")
META = UploadMeta("document", "application/pdf", "inv.pdf", len(PDF))


async def _users(storage: Storage) -> list[int]:
    async with storage.engine.connect() as conn:
        rows = await conn.exec_driver_sql("SELECT telegram_id FROM users ORDER BY 1")
        return [row[0] for row in rows]


# --- gc.collect after every document -----------------------------------------------------


class Collector:
    """Counts collections; each one takes 12 ms on the fake monotonic clock."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.calls = 0

    def __call__(self) -> int:
        self.calls += 1
        self.clock.mono += 0.012
        return 0


def _unavailable() -> ProviderUnavailableError:
    failed = (UsageRecord("gemini", "gemini-3.5-flash-lite", "provider_error", http_status=503),)
    return ProviderUnavailableError("gemini: unavailable", failed)


async def _download_fails() -> bytes:
    raise ConnectionError("telegram file server")


async def _download_garbage() -> bytes:
    return b"not an invoice at all"


async def _download_pdf() -> bytes:
    return PDF


@pytest.mark.parametrize(
    ("answer", "download", "kind"),
    [
        (truth_answer("inv-001"), _download_pdf, ResultKind.DONE),
        (truth_answer("inv-001"), _download_garbage, ResultKind.REJECTED),  # prepare refuses
        (_unavailable(), _download_pdf, ResultKind.BUSY),  # provider error
        (RuntimeError("bug"), _download_pdf, ResultKind.FAILED),  # unexpected error
        (truth_answer("inv-001"), _download_fails, ResultKind.FAILED),  # download error
    ],
    ids=["success", "prepare-refused", "provider-error", "unexpected-error", "download-error"],
)
def test_gc_runs_once_on_every_exit_from_the_slot(
    db_path: Path, clock: FakeClock, answer: object, download: object, kind: ResultKind
) -> None:
    collector = Collector(clock)

    async def scenario() -> ProcessingResult:
        async with open_storage(db_path) as storage:
            provider = FakeProvider(answer)  # type: ignore[arg-type]
            processor = make_processor(storage, provider, clock, collect=collector)

            async def notify(text: str) -> None:
                pass

            return await processor.process(1, META, download, notify)  # type: ignore[arg-type]

    assert asyncio.run(scenario()).kind is kind
    assert collector.calls == 1


def test_no_gc_when_the_upload_never_gets_a_slot(db_path: Path, clock: FakeClock) -> None:
    collector = Collector(clock)

    async def scenario() -> ResultKind:
        async with open_storage(db_path) as storage:
            processor = make_processor(
                storage,
                FakeProvider(truth_answer("inv-001")),
                clock,
                user_daily=0,
                collect=collector,
            )

            async def notify(text: str) -> None:
                pass

            return (await processor.process(1, META, _download_pdf, notify)).kind

    assert asyncio.run(scenario()) is ResultKind.LIMIT
    assert collector.calls == 0


def test_gc_ms_is_in_the_document_line(
    db_path: Path, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    collector = Collector(clock)

    async def scenario() -> None:
        async with open_storage(db_path) as storage:
            processor = make_processor(
                storage, FakeProvider(truth_answer("inv-001")), clock, collect=collector
            )

            async def notify(text: str) -> None:
                pass

            await processor.process(1, META, _download_pdf, notify)

    caplog.set_level(logging.INFO, logger="invoice_pilot.services.processing")
    asyncio.run(scenario())
    lines = [
        r.getMessage() for r in caplog.records if r.name == "invoice_pilot.services.processing"
    ]
    assert len(lines) == 1  # still one line per document
    assert lines[0].startswith("document processed: id=1 ")
    assert lines[0].endswith(" cost_usd=0.00155 gc_ms=12")


def test_processor_uses_gc_collect_by_default(db_path: Path, clock: FakeClock) -> None:
    import gc

    async def scenario() -> DocumentProcessor:
        async with open_storage(db_path) as storage:
            return make_processor(storage, FakeProvider("x"), clock)

    assert asyncio.run(scenario())._collect is gc.collect


# --- stale reservations ------------------------------------------------------------------


def _limits(storage: Storage, clock: FakeClock, **overrides: object) -> DailyLimits:
    values: dict[str, object] = {
        "user_daily_docs": 1,
        "global_daily_docs": 300,
        "global_daily_budget_usd": Decimal("0.50"),
    }
    values.update(overrides)
    return DailyLimits(storage, clock=clock, **values)  # type: ignore[arg-type]


def test_restart_frees_the_slot_and_the_held_budget(
    db_path: Path, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    budget = RESERVED_COST_PER_DOCUMENT_USD  # room for exactly one document in progress

    async def scenario() -> tuple[list[bool], int, list[bool], list[str]]:
        async with open_storage(db_path) as storage:
            limits = _limits(storage, clock, global_daily_budget_usd=budget)
            assert await limits.reserve(1) is not None  # the process dies here
            before = [
                await limits.reserve(1) is not None,  # user slot is taken
                await limits.reserve(2) is not None,  # the held budget blocks everyone
            ]
        async with open_storage(db_path) as storage:  # "restart"
            count = await fail_stale_reservations(storage)
            limits = _limits(storage, clock, global_daily_budget_usd=budget)
            remaining = await limits.remaining_today(1)
            after = [await limits.reserve(1) is not None]
            async with storage.transaction() as repo:
                statuses = [d.status for d in await repo.list_documents()]
        return before, count, after + [remaining == 1], statuses

    caplog.set_level(logging.INFO, logger="invoice_pilot.services.limits")
    before, count, after, statuses = asyncio.run(scenario())
    assert before == [False, False]
    assert count == 1
    assert after == [True, True]
    assert statuses == ["failed", "reserved"]
    assert [r.getMessage() for r in caplog.records] == [
        "startup: stale reservations marked failed=1"
    ]


def test_startup_leaves_finished_documents_alone(db_path: Path, clock: FakeClock) -> None:
    async def scenario() -> tuple[int, list[str]]:
        async with open_storage(db_path) as storage:
            processor = make_processor(storage, FakeProvider(truth_answer("inv-001")), clock)

            async def notify(text: str) -> None:
                pass

            await processor.process(1, META, _download_pdf, notify)
            await processor.process(1, META, _download_garbage, notify)
            count = await fail_stale_reservations(storage)
            async with storage.transaction() as repo:
                return count, [d.status for d in await repo.list_documents()]

    assert asyncio.run(scenario()) == (0, ["auto_approved", "rejected"])


def test_bot_marks_stale_reservations_before_polling() -> None:
    source = Path(__file__).resolve().parents[2] / "src" / "invoice_pilot" / "bot" / "app.py"
    text = source.read_text(encoding="utf-8")
    assert text.index("await fail_stale_reservations(storage)") < text.index("start_polling(")


# --- users are created only with a reservation ---------------------------------------------


def test_refusal_by_limit_creates_no_user(db_path: Path, clock: FakeClock) -> None:
    async def scenario() -> tuple[list[bool], list[int]]:
        async with open_storage(db_path) as storage:
            limits = _limits(storage, clock, global_daily_docs=1)
            results = [
                await limits.reserve(1) is not None,  # reserved: user 1 is created
                await limits.reserve(2) is not None,  # global limit: refused
                await limits.reserve(1) is not None,  # user limit: refused
            ]
            return results, await _users(storage)

    results, users = asyncio.run(scenario())
    assert results == [True, False, False]
    assert users == [1]
