"""Daily demo limits: per user, for all users, and the LLM budget (UTC days).

A document is counted from the moment its slot is reserved. Check and reserve
run atomically: under the user's lock, then the global lock, inside one
database transaction. aiogram handles updates concurrently, so without the
locks two uploads of one user could both see "1 left" and both pass.

The global lock is needed as well because the global counters are shared by
all users; it is held only for the few milliseconds of the check-and-insert.
Both locks are in-process: the Compact profile runs a single process.
"""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Final
from weakref import WeakValueDictionary

from invoice_pilot.storage import Storage

Clock = Callable[[], datetime]
LockFactory = Callable[[], AbstractAsyncContextManager[object]]

STATUS_RESERVED: Final = "reserved"
STATUS_AUTO_APPROVED: Final = "auto_approved"
STATUS_NEEDS_REVIEW: Final = "needs_review"
#: Refunded statuses: the slot is given back, the model was not used successfully.
STATUS_PROVIDER_ERROR: Final = "provider_error"
STATUS_REJECTED: Final = "rejected"
STATUS_FAILED: Final = "failed"
STATUS_QUEUE_FULL: Final = "queue_full"

#: Statuses that use up a daily slot.
COUNTED_STATUSES: Final[tuple[str, ...]] = (
    STATUS_RESERVED,
    STATUS_AUTO_APPROVED,
    STATUS_NEEDS_REVIEW,
)

#: Budget held for each document in progress, until its real cost is written.
#: Worst case of eval v1 (task 09): ~$0.0024 per call at paid-tier prices, and a
#: document may take two model calls (one retry after an unusable answer).
RESERVED_COST_PER_DOCUMENT_USD: Final = Decimal("0.005")


def day_start(now: datetime) -> datetime:
    """Start of the UTC day containing ``now``."""
    return datetime.combine(now.astimezone(UTC).date(), time(0), tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Reservation:
    document_id: int


class DailyLimits:
    """Reserve and release daily document slots."""

    def __init__(
        self,
        storage: Storage,
        *,
        user_daily_docs: int,
        global_daily_docs: int,
        global_daily_budget_usd: Decimal,
        clock: Clock,
        lock_factory: LockFactory = asyncio.Lock,
    ) -> None:
        self._storage = storage
        self.user_daily_docs = user_daily_docs
        self.global_daily_docs = global_daily_docs
        self.global_daily_budget_usd = global_daily_budget_usd
        self._clock = clock
        self._lock_factory = lock_factory
        self._user_locks: WeakValueDictionary[int, AbstractAsyncContextManager[object]] = (
            WeakValueDictionary()
        )
        self._global_lock = lock_factory()

    def _user_lock(self, user_id: int) -> AbstractAsyncContextManager[object]:
        lock = self._user_locks.get(user_id)
        if lock is None:
            lock = self._lock_factory()
            self._user_locks[user_id] = lock
        return lock

    async def reserve(self, user_id: int) -> Reservation | None:
        """Reserve one slot; None if any daily limit is reached (nothing is written then)."""
        user_lock = self._user_lock(user_id)
        async with user_lock, self._global_lock:
            now = self._clock()
            since = day_start(now)
            async with self._storage.transaction() as repo:
                await repo.ensure_user(user_id, now)
                used = await repo.count_documents(
                    since=since, statuses=COUNTED_STATUSES, user=user_id
                )
                if used >= self.user_daily_docs:
                    return None
                total = await repo.count_documents(since=since, statuses=COUNTED_STATUSES)
                if total >= self.global_daily_docs:
                    return None
                spent = await repo.spent_since(since)
                in_progress = await repo.count_documents(since=since, statuses=(STATUS_RESERVED,))
                held = RESERVED_COST_PER_DOCUMENT_USD * (in_progress + 1)
                if spent + held > self.global_daily_budget_usd:
                    return None
                document_id = await repo.add_document(user=user_id, now=now, status=STATUS_RESERVED)
            return Reservation(document_id)

    async def release(self, reservation: Reservation, status: str, user_id: int) -> None:
        """Give the slot back by moving the document to a non-counted ``status``."""
        if status in COUNTED_STATUSES:
            raise ValueError(f"status {status!r} would keep the slot")
        async with self._user_lock(user_id):
            async with self._storage.transaction() as repo:
                await repo.set_status(reservation.document_id, status)

    async def remaining_today(self, user_id: int) -> int:
        """Documents the user may still send today (also bounded by the global limit)."""
        since = day_start(self._clock())
        async with self._storage.transaction() as repo:
            used = await repo.count_documents(since=since, statuses=COUNTED_STATUSES, user=user_id)
            total = await repo.count_documents(since=since, statuses=COUNTED_STATUSES)
        return max(0, min(self.user_daily_docs - used, self.global_daily_docs - total))
