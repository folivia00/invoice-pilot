"""Daily deletion of extracted fields, call metadata and idle users past the retention period.

Runs inside the bot process at 05:00 UTC (the time agreed with the host). The
clock and sleep are injected so tests run without waiting.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from typing import Final

from invoice_pilot.storage import Storage

logger = logging.getLogger(__name__)

RUN_AT_UTC: Final = time(5, 0)

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]


def seconds_until_next_run(now: datetime, run_at: time = RUN_AT_UTC) -> float:
    """Seconds from ``now`` to the next ``run_at`` UTC (a full day if it is exactly now)."""
    now = now.astimezone(UTC)
    target = datetime.combine(now.date(), run_at, tzinfo=UTC)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def purge_expired(
    storage: Storage, *, now: datetime, retention_days: int
) -> tuple[int, int, int]:
    """Delete rows older than ``retention_days``: documents, their llm_calls, idle users.

    A user is deleted when first seen before the cutoff and no documents remain.
    Returns the counts (documents, llm_calls, users).
    """
    cutoff = now - timedelta(days=retention_days)
    async with storage.transaction() as repo:
        documents, calls = await repo.delete_older_than(cutoff)
        users = await repo.delete_inactive_users(cutoff)
    logger.info("retention: deleted documents=%d llm_calls=%d users=%d", documents, calls, users)
    return documents, calls, users


async def retention_loop(
    storage: Storage,
    *,
    retention_days: int,
    clock: Clock,
    sleep: Sleep = asyncio.sleep,
) -> None:
    """Sleep until 05:00 UTC, purge, repeat; runs until cancelled."""
    while True:
        await sleep(seconds_until_next_run(clock()))
        try:
            await purge_expired(storage, now=clock(), retention_days=retention_days)
        except Exception as exc:  # keep the loop alive; the next run tries again
            logger.error("retention failed: %s", type(exc).__name__)
