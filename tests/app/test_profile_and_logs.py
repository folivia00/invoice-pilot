"""Bot profile set at startup (commands, descriptions) and the export/JSON/stats log lines."""

import asyncio
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from helpers_app import FakeCallback, FakeClock, FakeMessage, open_storage
from test_export import OWNER, SEED, _seed

from invoice_pilot.bot.handlers import on_export_choice, on_json, on_stats
from invoice_pilot.bot.messages import BOT_DESCRIPTION, BOT_SHORT_DESCRIPTION
from invoice_pilot.bot.profile import PUBLIC_COMMANDS, publish_profile
from invoice_pilot.services.export import ExportService
from invoice_pilot.services.processing import ProcessingQueue
from invoice_pilot.services.stats import StatsService


class ProfileBot:
    """Records the profile calls; ``fail`` names the calls that raise."""

    def __init__(self, fail: tuple[str, ...] = ()) -> None:
        self.fail = fail
        self.calls: list[tuple[str, Any]] = []

    async def _call(self, name: str, value: Any) -> bool:
        if name in self.fail:
            raise ConnectionError("telegram")
        self.calls.append((name, value))
        return True

    async def set_my_commands(self, commands: list[Any]) -> bool:
        return await self._call("set_my_commands", [(c.command, c.description) for c in commands])

    async def set_my_short_description(self, short_description: str) -> bool:
        return await self._call("set_my_short_description", short_description)

    async def set_my_description(self, description: str) -> bool:
        return await self._call("set_my_description", description)


def test_profile_commands_and_descriptions(caplog: pytest.LogCaptureFixture) -> None:
    bot = ProfileBot()
    caplog.set_level(logging.INFO, logger="invoice_pilot.bot.profile")
    assert asyncio.run(publish_profile(bot)) == 3  # type: ignore[arg-type]
    assert bot.calls == [
        (
            "set_my_commands",
            [
                ("start", "About the demo and today's limit"),
                ("export", "Export your documents as CSV or XLSX"),
            ],
        ),
        ("set_my_short_description", BOT_SHORT_DESCRIPTION),
        ("set_my_description", BOT_DESCRIPTION),
    ]
    assert "stats" not in [c.command for c in PUBLIC_COMMANDS]  # admin-only, not advertised
    assert len(BOT_SHORT_DESCRIPTION) <= 120 and len(BOT_DESCRIPTION) <= 512
    for text in (BOT_SHORT_DESCRIPTION, BOT_DESCRIPTION):
        assert "demo" in text.lower() and "do not upload real" in text.lower()
    assert [r.getMessage() for r in caplog.records] == ["bot profile: set 3 of 3"]


def test_profile_failure_is_a_warning_and_the_rest_still_runs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bot = ProfileBot(fail=("set_my_commands", "set_my_description"))
    caplog.set_level(logging.INFO, logger="invoice_pilot.bot.profile")
    assert asyncio.run(publish_profile(bot)) == 1  # type: ignore[arg-type]
    assert [name for name, _ in bot.calls] == ["set_my_short_description"]
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("WARNING", "bot profile: set_my_commands failed: ConnectionError"),
        ("WARNING", "bot profile: set_my_description failed: ConnectionError"),
        ("INFO", "bot profile: set 1 of 3"),
    ]


def test_bot_publishes_the_profile_before_polling() -> None:
    source = Path(__file__).resolve().parents[2] / "src" / "invoice_pilot" / "bot" / "app.py"
    text = source.read_text(encoding="utf-8")
    assert text.index("await publish_profile(bot)") < text.index("start_polling(")


# --- Q-33 log lines ----------------------------------------------------------------------


def _handler_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "invoice_pilot.bot.handlers"]


@pytest.mark.parametrize(
    ("data", "line"),
    [
        ("export:csv", "export sent: format=csv rows=2"),
        ("export:xlsx", "export sent: format=xlsx rows=2"),
        ("json:2", "json sent"),
    ],
)
def test_export_and_json_log_one_line_without_ids_or_fields(
    db_path: Path, caplog: pytest.LogCaptureFixture, data: str, line: str
) -> None:
    callback = FakeCallback(data=data, user_id=OWNER)

    async def scenario() -> None:
        async with open_storage(db_path) as storage:
            await _seed(storage, SEED)
            service = ExportService(storage, retention_days=7, clock=FakeClock())
            handler = on_json if data.startswith("json:") else on_export_choice
            await handler(callback, service)  # type: ignore[arg-type]

    caplog.set_level(logging.INFO)
    asyncio.run(scenario())
    assert callback.bot.sent_documents
    assert _handler_lines(caplog) == [line]
    _assert_clean(caplog.text)


def test_nothing_to_export_and_unavailable_json_log_nothing(
    db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        async with open_storage(db_path) as storage:
            await _seed(storage, SEED)
            service = ExportService(storage, retention_days=7, clock=FakeClock())
            await on_export_choice(FakeCallback(data="export:csv", user_id=999), service)  # type: ignore[arg-type]
            await on_json(FakeCallback(data="json:5", user_id=OWNER), service)  # type: ignore[arg-type]

    caplog.set_level(logging.INFO)
    asyncio.run(scenario())
    assert _handler_lines(caplog) == []


def test_stats_logs_one_line_for_admins_only(
    db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    admin, user = 1000, 2000

    async def scenario() -> None:
        async with open_storage(db_path) as storage:
            service = StatsService(
                storage,
                admin_ids=frozenset({admin}),
                queue=ProcessingQueue(1),
                global_daily_docs=300,
                global_daily_budget_usd=Decimal("0.50"),
                retention_days=7,
                clock=FakeClock(),
            )
            for actor in (user, admin):
                await on_stats(FakeMessage(user_id=actor, text="/stats"), service)  # type: ignore[arg-type]

    caplog.set_level(logging.INFO)
    asyncio.run(scenario())
    assert _handler_lines(caplog) == ["stats shown"]
    assert str(admin) not in caplog.text and str(user) not in caplog.text


def _assert_clean(text: str) -> None:
    for forbidden in (str(OWNER), "Northwind", "3798.41", "4241.62", "invoices-", "invoice-2"):
        assert forbidden not in text
