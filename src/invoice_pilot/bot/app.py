"""Wiring: settings -> storage, limiter, provider, services, dispatcher; long polling.

Shutdown (SIGINT/SIGTERM, handled by aiogram on Linux; Ctrl+C on Windows)
stops polling, cancels the retention task and closes the HTTP client, the
database engine and the bot session.
"""

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime

import httpx
from aiogram import Bot, Dispatcher

from invoice_pilot.bot.handlers import build_router
from invoice_pilot.config import Settings
from invoice_pilot.extraction.prepare import InputLimits
from invoice_pilot.llm.config import LLMConfig
from invoice_pilot.llm.providers import make_provider
from invoice_pilot.services.limits import DailyLimits
from invoice_pilot.services.processing import DocumentProcessor, ProcessingQueue
from invoice_pilot.services.rate_limit import (
    RateLimitedProvider,
    SlidingWindowLimiter,
    limited_sleep,
)
from invoice_pilot.services.retention import retention_loop
from invoice_pilot.storage import Storage

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


class NotMigratedError(RuntimeError):
    pass


def build_processor(
    settings: Settings, storage: Storage, client: httpx.AsyncClient
) -> DocumentProcessor:
    """Services for one bot process (real clock, real sleep, Gemini provider)."""
    limiter = SlidingWindowLimiter(settings.llm_rpm, monotonic=time.monotonic)
    inner = make_provider(
        LLMConfig(provider="gemini", model=settings.llm_model),
        api_key=settings.gemini_api_key,
        client=client,
        sleep=limited_sleep(limiter),
    )
    limits = DailyLimits(
        storage,
        user_daily_docs=settings.user_daily_docs,
        global_daily_docs=settings.global_daily_docs,
        global_daily_budget_usd=settings.global_daily_budget_usd,
        clock=utc_now,
    )
    return DocumentProcessor(
        storage=storage,
        limits=limits,
        provider=RateLimitedProvider(inner, limiter),
        queue=ProcessingQueue(settings.concurrency),
        input_limits=InputLimits(max_bytes=settings.max_file_bytes, max_pages=settings.max_pages),
        clock=utc_now,
    )


async def run(settings: Settings) -> None:
    """Run the bot until polling stops."""
    if not settings.database_path.is_file():
        raise NotMigratedError("database not found: run `uv run alembic upgrade head` first")
    storage = Storage.for_path(settings.database_path)
    client = httpx.AsyncClient()
    bot = Bot(settings.bot_token)
    retention: asyncio.Task[None] | None = None
    try:
        if not await storage.schema_ready():
            raise NotMigratedError("database is not migrated: run `uv run alembic upgrade head`")
        dispatcher = Dispatcher()
        dispatcher.include_router(build_router())
        dispatcher["processor"] = build_processor(settings, storage, client)
        dispatcher["retention_days"] = settings.retention_days
        retention = asyncio.create_task(
            retention_loop(storage, retention_days=settings.retention_days, clock=utc_now)
        )
        logger.info(
            "bot starting: profile=%s model=%s llm_rpm=%d concurrency=%d",
            settings.profile,
            settings.llm_model,
            settings.llm_rpm,
            settings.concurrency,
        )
        await dispatcher.start_polling(bot, handle_signals=True, close_bot_session=True)
    finally:
        if retention is not None:
            retention.cancel()
            with suppress(asyncio.CancelledError):
                await retention
        await client.aclose()
        await storage.dispose()
        await bot.session.close()
        logger.info("bot stopped")
