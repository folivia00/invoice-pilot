"""Operational statistics for admins (/stats): counts and money only.

Access is decided here by the actor's Telegram ID against ``ADMIN_IDS``; the
handler never checks rights itself. The snapshot holds no document content and
no user IDs.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from invoice_pilot.services.limits import COUNTED_STATUSES, day_start
from invoice_pilot.services.processing import ProcessingQueue
from invoice_pilot.storage import Storage

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class PeriodStats:
    documents: dict[str, int]
    users: int
    calls_ok: int
    calls_429: int
    calls_other_errors: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


@dataclass(frozen=True, slots=True)
class StatsSnapshot:
    today: PeriodStats
    retention: PeriodStats
    retention_days: int
    docs_left_today: int
    budget_left_today_usd: Decimal
    queue_waiting: int
    queue_active: int


class StatsService:
    def __init__(
        self,
        storage: Storage,
        *,
        admin_ids: frozenset[int],
        queue: ProcessingQueue,
        global_daily_docs: int,
        global_daily_budget_usd: Decimal,
        retention_days: int,
        clock: Clock,
    ) -> None:
        self._storage = storage
        self._admin_ids = admin_ids
        self._queue = queue
        self._global_daily_docs = global_daily_docs
        self._budget = global_daily_budget_usd
        self._retention_days = retention_days
        self._clock = clock

    def is_admin(self, user_id: int) -> bool:
        return user_id in self._admin_ids

    async def _period(self, since: datetime) -> PeriodStats:
        async with self._storage.transaction() as repo:
            documents = await repo.status_counts(since=since)
            users = await repo.distinct_users(since=since)
            groups = await repo.call_totals(since=since)
            cost = await repo.spent_since(since)
        ok = sum(c for status, _, c, _, _ in groups if status == "ok")
        rate_limited = sum(c for status, http, c, _, _ in groups if status != "ok" and http == 429)
        other = sum(c for status, http, c, _, _ in groups if status != "ok" and http != 429)
        return PeriodStats(
            documents=dict(sorted(documents.items())),
            users=users,
            calls_ok=ok,
            calls_429=rate_limited,
            calls_other_errors=other,
            input_tokens=sum(i for _, _, _, i, _ in groups),
            output_tokens=sum(o for _, _, _, _, o in groups),
            cost_usd=cost,
        )

    async def snapshot(self, actor_id: int) -> StatsSnapshot | None:
        """Statistics for an admin; None for anyone else (the command stays hidden)."""
        if not self.is_admin(actor_id):
            return None
        now = self._clock()
        today = await self._period(day_start(now))
        retention = await self._period(now - timedelta(days=self._retention_days))
        counted = sum(n for status, n in today.documents.items() if status in COUNTED_STATUSES)
        return StatsSnapshot(
            today=today,
            retention=retention,
            retention_days=self._retention_days,
            docs_left_today=max(0, self._global_daily_docs - counted),
            budget_left_today_usd=max(Decimal(0), self._budget - today.cost_usd),
            queue_waiting=self._queue.waiting,
            queue_active=self._queue.active,
        )
