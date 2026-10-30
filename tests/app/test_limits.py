"""Daily limits: per user, global, budget; the concurrent-upload race; refunds."""

import asyncio
import contextlib
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from helpers_app import (
    NOW,
    FakeClock,
    FakeProvider,
    dataset_bytes,
    make_processor,
    open_storage,
    truth_answer,
)

from invoice_pilot.llm.errors import ProviderUnavailableError, SpendLimitError
from invoice_pilot.llm.types import UsageRecord
from invoice_pilot.services.limits import RESERVED_COST_PER_DOCUMENT_USD
from invoice_pilot.services.processing import (
    MSG_BUSY,
    MSG_LIMIT,
    DocumentProcessor,
    ProcessingResult,
    ResultKind,
    UploadMeta,
)
from invoice_pilot.storage import CallRow

PDF = dataset_bytes("inv-001")
META = UploadMeta("document", "application/pdf", "inv.pdf", len(PDF))


async def upload(processor: DocumentProcessor, user: int = 1) -> ProcessingResult:
    async def download() -> bytes:
        return PDF

    async def notify(text: str) -> None:
        pass

    return await processor.process(user, META, download, notify)


def test_user_daily_limit_and_reset_next_utc_day(db_path: Path, clock: FakeClock) -> None:
    async def scenario() -> list[ResultKind]:
        async with open_storage(db_path) as storage:
            provider = FakeProvider(truth_answer("inv-001"))
            processor = make_processor(storage, provider, clock, user_daily=2)
            kinds = [(await upload(processor)).kind for _ in range(3)]
            assert await processor.remaining_today(1) == 0
            assert (await upload(processor, user=2)).kind is ResultKind.DONE  # other user
            assert provider.calls == 3
            clock.now = NOW.replace(hour=23, minute=59, second=59)
            kinds.append((await upload(processor)).kind)
            clock.now = NOW.replace(hour=0) + timedelta(days=1)  # next UTC day
            kinds.append((await upload(processor)).kind)
            return kinds

    assert asyncio.run(scenario()) == [
        ResultKind.DONE,
        ResultKind.DONE,
        ResultKind.LIMIT,
        ResultKind.LIMIT,
        ResultKind.DONE,
    ]


def test_limit_message_and_no_model_call(db_path: Path, clock: FakeClock) -> None:
    async def scenario() -> tuple[ProcessingResult, int]:
        async with open_storage(db_path) as storage:
            provider = FakeProvider(truth_answer("inv-001"))
            processor = make_processor(storage, provider, clock, user_daily=0)
            return await upload(processor), provider.calls

    result, calls = asyncio.run(scenario())
    assert result.kind is ResultKind.LIMIT
    assert (
        result.message
        == MSG_LIMIT
        == ("Demo limit reached for today, please try again tomorrow (UTC).")
    )
    assert calls == 0


def test_global_daily_document_limit(db_path: Path, clock: FakeClock) -> None:
    async def scenario() -> list[ResultKind]:
        async with open_storage(db_path) as storage:
            processor = make_processor(
                storage, FakeProvider(truth_answer("inv-001")), clock, global_daily=3
            )
            return [(await upload(processor, user=u)).kind for u in (1, 2, 3, 4, 1)]

    kinds = asyncio.run(scenario())
    assert kinds == [ResultKind.DONE] * 3 + [ResultKind.LIMIT] * 2


def test_global_daily_budget(db_path: Path, clock: FakeClock) -> None:
    async def scenario() -> tuple[ResultKind, ResultKind, ResultKind]:
        async with open_storage(db_path) as storage:
            budget = Decimal("0.10")
            processor = make_processor(
                storage, FakeProvider(truth_answer("inv-001")), clock, budget=budget
            )
            async with storage.transaction() as repo:
                await repo.ensure_user(99, NOW)
                doc = await repo.add_document(user=99, now=NOW, status="auto_approved")
                spent = budget - RESERVED_COST_PER_DOCUMENT_USD
                call = CallRow("gemini", "m", "ok", 200, 1, 1, 0, 1, spent)
                await repo.add_llm_calls(doc, NOW, [call])
            first = (await upload(processor)).kind  # spent + held == budget: allowed
            second = (await upload(processor)).kind  # the first call's cost pushed it over
            async with storage.transaction() as repo:
                yesterday = NOW - timedelta(days=1)
                await repo.add_llm_calls(doc, yesterday, [call])  # other days do not count
            return first, second, (await upload(processor, user=5)).kind

    assert asyncio.run(scenario()) == (ResultKind.DONE, ResultKind.LIMIT, ResultKind.LIMIT)


def _race(db_path: Path, clock: FakeClock, lock_factory: object) -> list[ResultKind]:
    """Two uploads of one user at the same time, one slot left."""

    async def scenario() -> list[ResultKind]:
        async with open_storage(db_path) as storage:
            processor = make_processor(
                storage,
                FakeProvider(truth_answer("inv-001")),
                clock,
                user_daily=2,
                lock_factory=lock_factory,  # type: ignore[arg-type]
            )
            assert (await upload(processor)).kind is ResultKind.DONE  # one slot left
            results = await asyncio.gather(upload(processor), upload(processor))
            return sorted((r.kind for r in results), key=lambda k: k.value)

    return asyncio.run(scenario())


def test_race_two_uploads_one_slot_left(db_path: Path, clock: FakeClock) -> None:
    assert _race(db_path, clock, asyncio.Lock) == [ResultKind.DONE, ResultKind.LIMIT]


def test_race_without_locks_overbooks(db_path: Path, clock: FakeClock) -> None:
    """Proof that the locks are needed: with no-op locks both uploads pass the check."""
    assert _race(db_path, clock, contextlib.nullcontext) == [ResultKind.DONE, ResultKind.DONE]


def test_provider_unavailable_returns_the_slot(db_path: Path, clock: FakeClock) -> None:
    failed = (
        UsageRecord(
            "gemini", "gemini-3.5-flash-lite", "provider_error", latency_ms=90, http_status=429
        ),
    ) * 3

    async def scenario() -> None:
        async with open_storage(db_path) as storage:
            error = ProviderUnavailableError("gemini: unavailable after 3 attempts", failed)
            provider = FakeProvider(error, truth_answer("inv-001"))
            processor = make_processor(storage, provider, clock, user_daily=1)
            result = await upload(processor)
            assert result.kind is ResultKind.BUSY
            assert (
                result.message
                == MSG_BUSY
                == ("The AI service is busy right now. Please try again in a minute.")
            )
            assert await processor.remaining_today(1) == 1  # refunded
            async with storage.transaction() as repo:
                docs = await repo.list_documents()
                calls = await repo.list_llm_calls()
            assert [d.status for d in docs] == ["provider_error"]
            assert [c.http_status for c in calls] == [429, 429, 429]
            assert all(Decimal(c.cost_usd) == 0 for c in calls)
            assert (await upload(processor)).kind is ResultKind.DONE  # the slot is usable

    asyncio.run(scenario())


def test_spend_limit_error_is_busy_and_refunded(db_path: Path, clock: FakeClock) -> None:
    async def scenario() -> None:
        async with open_storage(db_path) as storage:
            error = SpendLimitError("gemini: spend limit reached", code="payment_required")
            processor = make_processor(storage, FakeProvider(error), clock, user_daily=1)
            assert (await upload(processor)).kind is ResultKind.BUSY
            assert await processor.remaining_today(1) == 1

    asyncio.run(scenario())
