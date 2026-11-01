"""API process: storage, processor, retention task, aiohttp server until SIGINT/SIGTERM."""

import asyncio
import logging
import signal
from contextlib import suppress

import httpx
from aiohttp import web

from invoice_pilot.api.app import build_app
from invoice_pilot.config import Settings
from invoice_pilot.services.limits import fail_stale_reservations
from invoice_pilot.services.retention import retention_loop
from invoice_pilot.services.wiring import build_processor, utc_now
from invoice_pilot.storage import Storage

logger = logging.getLogger(__name__)


class NotMigratedError(RuntimeError):
    pass


async def run(settings: Settings, stop: asyncio.Event | None = None) -> None:
    """Serve until ``stop`` is set (by default: on SIGINT or SIGTERM)."""
    if not settings.database_path.is_file():
        raise NotMigratedError("database not found: run `uv run alembic upgrade head` first")
    stop = stop or _stop_on_signals()
    storage = Storage.for_path(settings.database_path)
    client = httpx.AsyncClient()
    runner: web.AppRunner | None = None
    retention: asyncio.Task[None] | None = None
    try:
        if not await storage.schema_ready():
            raise NotMigratedError("database is not migrated: run `uv run alembic upgrade head`")
        await fail_stale_reservations(storage)
        processor = build_processor(
            settings, storage, client, user_daily_docs=settings.api_daily_docs
        )
        app = build_app(
            processor, api_token=settings.api_token, max_file_bytes=settings.max_file_bytes
        )
        # access_log=None: aiohttp's access log would record client addresses
        runner = web.AppRunner(app, access_log=None, handle_signals=False)
        await runner.setup()
        await web.TCPSite(runner, settings.api_host, settings.api_port).start()
        retention = asyncio.create_task(
            retention_loop(storage, retention_days=settings.retention_days, clock=utc_now)
        )
        logger.info(
            "api starting: profile=%s model=%s host=%s port=%d daily_docs=%d",
            settings.profile,
            processor.model,
            settings.api_host,
            settings.api_port,
            settings.api_daily_docs,
        )
        await stop.wait()
    finally:
        if retention is not None:
            retention.cancel()
            with suppress(asyncio.CancelledError):
                await retention
        if runner is not None:
            await runner.cleanup()
        await client.aclose()
        await storage.dispose()
        logger.info("api stopped")


def _stop_on_signals() -> asyncio.Event:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):  # Windows: Ctrl+C raises KeyboardInterrupt
            loop.add_signal_handler(sig, stop.set)
    return stop
