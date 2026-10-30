"""/stats (admin only), queue without bytes (download at slot), overflow, user cleanup."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from helpers_app import (
    NOW,
    FakeClock,
    FakeMessage,
    FakeProvider,
    dataset_bytes,
    make_processor,
    open_storage,
    truth_answer,
)

from invoice_pilot.bot.handlers import on_stats
from invoice_pilot.bot.messages import HINT, stats_text
from invoice_pilot.services.processing import (
    MSG_DOWNLOAD_FAILED,
    MSG_QUEUE_FULL,
    ProcessingQueue,
    ProcessingResult,
    ResultKind,
    UploadMeta,
    queue_message,
)
from invoice_pilot.services.retention import purge_expired
from invoice_pilot.services.stats import StatsService
from invoice_pilot.storage import CallRow, Storage

ADMIN, USER = 1000, 2000
PDF = dataset_bytes("inv-001")
META = UploadMeta("document", "application/pdf", "inv.pdf", len(PDF))

# --- /stats ----------------------------------------------------------------------------


async def _seed_stats(storage: Storage) -> None:
    async with storage.transaction() as repo:
        for user in (1, 2, 3):
            await repo.ensure_user(user, NOW - timedelta(days=10))
        plan = [
            (
                1,
                "auto_approved",
                0,
                [CallRow("gemini", "m", "ok", 200, 1000, 500, 0, 1, Decimal("0.00155"))],
            ),
            (
                1,
                "needs_review",
                0,
                [CallRow("gemini", "m", "ok", 200, 900, 400, 0, 1, Decimal("0.00127"))],
            ),
            (
                2,
                "provider_error",
                0,
                [CallRow("gemini", "m", "provider_error", 429, 0, 0, 0, 1, Decimal(0))] * 3,
            ),
            (
                2,
                "auto_approved",
                0,
                [
                    CallRow("gemini", "m", "provider_error", 503, 0, 0, 0, 1, Decimal(0)),
                    CallRow(
                        "gemini", "m", "invalid_response", 200, 800, 300, 0, 1, Decimal("0.00099")
                    ),
                    CallRow("gemini", "m", "ok", 200, 800, 300, 0, 1, Decimal("0.00099")),
                ],
            ),
            (
                3,
                "auto_approved",
                3,
                [CallRow("gemini", "m", "ok", 200, 1200, 600, 0, 1, Decimal("0.00186"))],
            ),
            (3, "rejected", 3, []),
        ]
        for user, status, age, calls in plan:
            created = NOW - timedelta(days=age)
            doc = await repo.add_document(user=user, now=created, status=status)
            await repo.add_llm_calls(doc, created, calls)


def _stats(db_path: Path, actor: int, queue: ProcessingQueue | None = None) -> object:
    async def scenario() -> object:
        async with open_storage(db_path) as storage:
            await _seed_stats(storage)
            service = StatsService(
                storage,
                admin_ids=frozenset({ADMIN}),
                queue=queue or ProcessingQueue(1),
                global_daily_docs=300,
                global_daily_budget_usd=Decimal("0.50"),
                retention_days=7,
                clock=FakeClock(),
            )
            return await service.snapshot(actor)

    return asyncio.run(scenario())


def test_stats_values_for_admin(db_path: Path) -> None:
    snap = _stats(db_path, ADMIN)
    today, period = snap.today, snap.retention  # type: ignore[attr-defined]
    assert today.documents == {"auto_approved": 2, "needs_review": 1, "provider_error": 1}
    assert today.users == 2
    assert (today.calls_ok, today.calls_429, today.calls_other_errors) == (3, 3, 2)
    assert (today.input_tokens, today.output_tokens) == (
        1000 + 900 + 800 + 800,
        500 + 400 + 300 + 300,
    )
    assert today.cost_usd == Decimal("0.00480")
    assert period.documents == {
        "auto_approved": 3,
        "needs_review": 1,
        "provider_error": 1,
        "rejected": 1,
    }
    assert period.users == 3
    assert period.cost_usd == Decimal("0.00666")
    assert snap.docs_left_today == 300 - 3  # type: ignore[attr-defined]
    assert snap.budget_left_today_usd == Decimal("0.49520")  # type: ignore[attr-defined]


def test_stats_hidden_from_non_admin(db_path: Path) -> None:
    assert _stats(db_path, USER) is None


def test_stats_text_has_no_user_ids_or_content(db_path: Path) -> None:
    queue = ProcessingQueue(1)
    text = stats_text(_stats(db_path, ADMIN, queue))  # type: ignore[arg-type]
    assert "Documents: 4 (auto_approved 2, needs_review 1, provider_error 1)" in text
    assert "LLM calls: ok 3 · 429 3 · other errors 2" in text
    assert "Cost: $0.0048 (paid-tier prices)" in text
    assert "<b>Left today:</b> 297 documents · $0.4952" in text
    assert "<b>Queue:</b> 0 waiting · 0 processing" in text
    for forbidden in (str(ADMIN), str(USER), "Northwind"):
        assert forbidden not in text


def test_stats_handler_admin_and_non_admin(db_path: Path) -> None:
    def run(user: int) -> FakeMessage:
        message = FakeMessage(user_id=user, text="/stats")

        async def scenario() -> None:
            async with open_storage(db_path) as storage:
                service = StatsService(
                    storage,
                    admin_ids=frozenset({ADMIN}),
                    queue=ProcessingQueue(1),
                    global_daily_docs=300,
                    global_daily_budget_usd=Decimal("0.50"),
                    retention_days=7,
                    clock=FakeClock(),
                )
                await on_stats(message, service)  # type: ignore[arg-type]

        asyncio.run(scenario())
        return message

    assert run(USER).texts == [HINT]  # same as an unknown command
    assert run(ADMIN).texts[0].startswith("<b>Stats</b> (UTC)")


# --- queue: no bytes while waiting -------------------------------------------------------


def test_waiting_uploads_hold_no_bytes_download_happens_at_slot(
    db_path: Path, clock: FakeClock
) -> None:
    async def scenario() -> tuple[
        list[str], list[str], dict[int, list[str]], list[ProcessingResult]
    ]:
        async with open_storage(db_path) as storage:
            gate = asyncio.Event()
            provider = FakeProvider(truth_answer("inv-001"), gate=gate)
            processor = make_processor(storage, provider, clock, user_daily=10)
            events: list[str] = []
            notices: dict[int, list[str]] = {}

            async def run(user: int) -> ProcessingResult:
                notices[user] = []

                async def download() -> bytes:
                    events.append(f"download {user}")
                    return PDF

                async def notify(text: str) -> None:
                    notices[user].append(text)

                return await processor.process(user, META, download, notify)

            tasks = [asyncio.create_task(run(1))]
            await provider.started.wait()
            tasks += [asyncio.create_task(run(u)) for u in (2, 3, 4)]
            while sum(len(n) for n in notices.values()) < 3:
                await asyncio.sleep(0.01)
            while_waiting = list(events)  # three uploads are in line now
            gate.set()
            results = list(await asyncio.gather(*tasks))
            return while_waiting, events, notices, results

    while_waiting, events, notices, results = asyncio.run(scenario())
    assert while_waiting == ["download 1"]  # nobody in line has downloaded anything
    assert events == ["download 1", "download 2", "download 3", "download 4"]
    assert [notices[u] for u in (1, 2, 3, 4)] == [
        [],
        [queue_message(1)],
        [queue_message(2)],
        [queue_message(3)],
    ]
    assert all(r.kind is ResultKind.DONE for r in results)


def test_full_queue_refuses_and_returns_the_slot(db_path: Path, clock: FakeClock) -> None:
    async def scenario() -> tuple[list[ResultKind], ProcessingResult, int, int]:
        async with open_storage(db_path) as storage:
            gate = asyncio.Event()
            provider = FakeProvider(truth_answer("inv-001"), gate=gate)
            processor = make_processor(storage, provider, clock, user_daily=10, queue_max=2)
            downloads: list[int] = []

            async def run(user: int) -> ProcessingResult:
                async def download() -> bytes:
                    downloads.append(user)
                    return PDF

                async def notify(text: str) -> None:
                    pass

                return await processor.process(user, META, download, notify)

            first = asyncio.create_task(run(1))
            await provider.started.wait()
            waiting = [asyncio.create_task(run(u)) for u in (2, 3)]
            await asyncio.sleep(0.05)
            refused = await run(4)  # line holds 2: refused at once
            remaining = await processor.remaining_today(4)
            gate.set()
            done = [r.kind for r in await asyncio.gather(first, *waiting)]
            return done, refused, remaining, 4 in downloads

    done, refused, remaining, downloaded = asyncio.run(scenario())
    assert done == [ResultKind.DONE] * 3
    assert refused.kind is ResultKind.BUSY
    assert (
        refused.message
        == MSG_QUEUE_FULL
        == ("The demo is busy right now. Please try again in a minute.")
    )
    assert remaining == 10  # the reservation was returned
    assert downloaded is False


def test_download_failure_at_slot_returns_the_slot(db_path: Path, clock: FakeClock) -> None:
    async def scenario() -> tuple[ProcessingResult, int, int]:
        async with open_storage(db_path) as storage:
            provider = FakeProvider(truth_answer("inv-001"))
            processor = make_processor(storage, provider, clock, user_daily=1)

            async def download() -> bytes:
                raise ConnectionError("telegram file server")

            async def notify(text: str) -> None:
                pass

            result = await processor.process(1, META, download, notify)
            return result, await processor.remaining_today(1), provider.calls

    result, remaining, calls = asyncio.run(scenario())
    assert result.kind is ResultKind.FAILED
    assert result.message == MSG_DOWNLOAD_FAILED
    assert remaining == 1
    assert calls == 0


# --- retention of users ----------------------------------------------------------------


def test_purge_deletes_idle_users_only(db_path: Path) -> None:
    async def scenario() -> tuple[tuple[int, int, int], list[int]]:
        async with open_storage(db_path) as storage:
            async with storage.transaction() as repo:
                await repo.ensure_user(1, NOW - timedelta(days=20))  # old, no documents -> gone
                await repo.ensure_user(2, NOW - timedelta(days=20))  # old, recent document -> kept
                await repo.ensure_user(3, NOW - timedelta(days=2))  # new, no documents -> kept
                await repo.ensure_user(
                    4, NOW - timedelta(days=20)
                )  # old, only old document -> gone
                await repo.add_document(user=2, now=NOW - timedelta(days=1), status="auto_approved")
                await repo.add_document(user=4, now=NOW - timedelta(days=9), status="auto_approved")
            deleted = await purge_expired(storage, now=NOW, retention_days=7)
            async with storage.engine.connect() as conn:
                rows = await conn.exec_driver_sql("SELECT telegram_id FROM users ORDER BY 1")
                users = [row[0] for row in rows]
            return deleted, users

    deleted, users = asyncio.run(scenario())
    assert deleted == (1, 0, 2)
    assert users == [2, 3]
