"""Retention job, log hygiene and the import guard of the bot process."""

import asyncio
import json
import logging
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from helpers_app import (
    NOW,
    REPO,
    FakeClock,
    FakeProvider,
    answer_json,
    dataset_bytes,
    document_message,
    make_processor,
    open_storage,
)

from invoice_pilot.bot.handlers import on_document
from invoice_pilot.logging_setup import QUIET_LOGGERS, apply_quiet_levels, configure_logging
from invoice_pilot.services.retention import (
    purge_expired,
    retention_loop,
    seconds_until_next_run,
)
from invoice_pilot.storage import CallRow
from invoice_pilot.storage.tables import Document

# --- retention -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 10, 31, 4, 59, tzinfo=UTC), 60),
        (datetime(2026, 10, 31, 5, 0, tzinfo=UTC), 24 * 3600),
        (datetime(2026, 10, 31, 5, 0, 1, tzinfo=UTC), 24 * 3600 - 1),
        (datetime(2026, 10, 31, 23, 0, tzinfo=UTC), 6 * 3600),
    ],
)
def test_seconds_until_next_run(now: datetime, expected: float) -> None:
    assert seconds_until_next_run(now) == expected


async def _seed(storage: object, ages_days: list[float]) -> None:
    async with storage.transaction() as repo:  # type: ignore[attr-defined]
        await repo.ensure_user(1, NOW - timedelta(days=30))
        for age in ages_days:
            created = NOW - timedelta(days=age)
            doc = await repo.add_document(user=1, now=created, status="auto_approved")
            call = CallRow("gemini", "m", "ok", 200, 1, 1, 0, 1, 0)  # type: ignore[arg-type]
            await repo.add_llm_calls(doc, created, [call])


def test_purge_deletes_only_expired_rows(db_path: Path) -> None:
    async def scenario() -> tuple[tuple[int, int, int], list[Document], int]:
        async with open_storage(db_path) as storage:
            await _seed(storage, [10, 7.01, 6.99, 0])
            deleted = await purge_expired(storage, now=NOW, retention_days=7)
            async with storage.transaction() as repo:
                return deleted, await repo.list_documents(), len(await repo.list_llm_calls())

    deleted, docs, calls = asyncio.run(scenario())
    assert deleted == (2, 2, 0)  # the user still has documents
    assert sorted(round((NOW - d.created_at).total_seconds() / 86400, 2) for d in docs) == [0, 6.99]
    assert calls == 2


def test_retention_loop_runs_at_0500_utc(db_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock(datetime(2026, 10, 31, 4, 30, tzinfo=UTC))
    runs: list[datetime] = []

    async def sleep(seconds: float) -> None:
        clock.sleeps.append(seconds)
        if len(clock.sleeps) == 3:
            raise asyncio.CancelledError
        clock.advance(seconds)
        runs.append(clock.now)

    async def scenario() -> None:
        async with open_storage(db_path) as storage:
            await _seed(storage, [20])
            with pytest.raises(asyncio.CancelledError):
                await retention_loop(storage, retention_days=7, clock=clock, sleep=sleep)

    caplog.set_level(logging.INFO, logger="invoice_pilot.services.retention")
    asyncio.run(scenario())
    assert clock.sleeps[:2] == [1800, 24 * 3600]
    assert runs == [
        datetime(2026, 10, 31, 5, 0, tzinfo=UTC),
        datetime(2026, 11, 1, 5, 0, tzinfo=UTC),
    ]
    lines = [r.getMessage() for r in caplog.records if r.name.endswith("retention")]
    assert lines == [
        "retention: deleted documents=1 llm_calls=1 users=1",
        "retention: deleted documents=0 llm_calls=0 users=0",
    ]


# --- logging ---------------------------------------------------------------------------

MARKER = "MARKER-SECRET-CO"
MARKER_IBAN = "DE89370400440532013000"


def test_logs_carry_no_document_content(
    db_path: Path, clock: FakeClock, caplog: pytest.LogCaptureFixture, fake_api_key: str
) -> None:
    invoice = json.loads(
        (REPO / "eval" / "dataset" / "ground_truth" / "inv-001.json").read_text(encoding="utf-8")
    )["invoice"]
    invoice["supplier"]["name"] = MARKER
    invoice["iban"] = MARKER_IBAN
    message = document_message(
        dataset_bytes("inv-001"), file_name="inv-001.pdf", mime_type="application/pdf"
    )

    async def scenario() -> None:
        async with open_storage(db_path) as storage:
            processor = make_processor(storage, FakeProvider(answer_json(invoice)), clock)
            await on_document(message, processor)  # type: ignore[arg-type]

    apply_quiet_levels()  # the production levels, with the root at DEBUG (worst case)
    caplog.set_level(logging.DEBUG)
    try:
        asyncio.run(scenario())
    finally:
        for name in QUIET_LOGGERS:
            logging.getLogger(name).setLevel(logging.NOTSET)
    assert MARKER in message.texts[0]  # the user sees it ...
    text = caplog.text
    assert MARKER not in text  # ... the log does not
    assert MARKER_IBAN not in text and "DE89 3704" not in text
    assert "Northwind" not in text and "3798.41" not in text
    assert fake_api_key not in text
    processed = [r for r in caplog.records if r.getMessage().startswith("document processed")]
    assert len(processed) == 1
    line = processed[0].getMessage()
    for key in ("id=1", "input_kind=text_pdf", "status=", "issues=", "model=gemini-3.5-flash-lite"):
        assert key in line
    for key in ("tokens_in=1000", "tokens_out=500", "latency_ms=", "cost_usd=0.00155"):
        assert key in line


def test_configure_logging_quiets_libraries_and_uses_stdout() -> None:
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    try:
        configure_logging("INFO")
        assert len(root.handlers) == 1
        assert root.handlers[0].stream is sys.stdout  # type: ignore[attr-defined]
        assert logging.getLogger("aiogram.event").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
        assert logging.getLogger("pypdf").level == logging.ERROR
        assert set(QUIET_LOGGERS) >= {"aiogram.event", "httpx", "httpcore", "pypdf"}
        record = logging.LogRecord("x", logging.INFO, "", 0, "msg", None, None)
        record.created = datetime(2026, 10, 31, 12, 0, 5, tzinfo=UTC).timestamp()
        assert root.handlers[0].format(record) == "2026-10-31T12:00:05Z INFO x msg"
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
        for name in QUIET_LOGGERS:
            logging.getLogger(name).setLevel(logging.NOTSET)


# --- import guard ----------------------------------------------------------------------


def test_bot_import_does_not_load_heavy_or_cli_modules() -> None:
    code = (
        "import sys, invoice_pilot.bot, invoice_pilot.bot.app, invoice_pilot.bot.handlers; "
        "print(','.join(m for m in ('alembic', 'openpyxl', 'PIL', 'pypdf') if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=REPO
    )
    assert result.stdout.strip() == ""
