"""Engine, transactions and the repository: the only place in the project with SQL.

Services open ``Storage.transaction()`` and call repository methods; the
transaction commits on normal exit and rolls back on an exception. Decisions
(limits, statuses) belong to the services, not to this module.
"""

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from sqlalchemy import delete, event, func, inspect, select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from invoice_pilot.storage.tables import Document, LLMCall, User

#: Seconds a writer waits for SQLite's write lock before failing.
BUSY_TIMEOUT_MS: Final = 5000


def sqlite_url(path: Path, *, driver: str = "aiosqlite") -> str:
    """SQLAlchemy URL of the SQLite file at ``path`` (``driver=""`` for the stdlib driver)."""
    dialect = f"sqlite+{driver}" if driver else "sqlite"
    return f"{dialect}:///{path.as_posix()}"


@dataclass(frozen=True, slots=True)
class CallRow:
    """One provider HTTP call as stored in ``llm_calls``."""

    provider: str
    model: str
    status: str
    http_status: int | None
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    latency_ms: int
    cost_usd: Decimal


class Repository:
    """Queries inside one open transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_user(self, telegram_id: int, now: datetime) -> None:
        if await self._session.get(User, telegram_id) is None:
            self._session.add(User(telegram_id=telegram_id, first_seen_at=now))
            await self._session.flush()

    async def count_documents(
        self, *, since: datetime, statuses: Iterable[str], user: int | None = None
    ) -> int:
        query = select(func.count(Document.id)).where(
            Document.created_at >= since, Document.status.in_(list(statuses))
        )
        if user is not None:
            query = query.where(Document.user_telegram_id == user)
        return int((await self._session.execute(query)).scalar_one())

    async def spent_since(self, since: datetime) -> Decimal:
        """Exact sum of ``llm_calls.cost_usd`` since ``since`` (summed as Decimal in Python)."""
        rows = await self._session.execute(
            select(LLMCall.cost_usd).where(LLMCall.created_at >= since)
        )
        return sum((Decimal(value) for value in rows.scalars()), Decimal(0))

    async def add_document(self, *, user: int, now: datetime, status: str) -> int:
        doc = Document(user_telegram_id=user, created_at=now, status=status)
        self._session.add(doc)
        await self._session.flush()
        return doc.id

    async def set_status(self, document_id: int, status: str) -> None:
        await self._session.execute(
            update(Document).where(Document.id == document_id).values(status=status)
        )

    async def finish_document(
        self,
        document_id: int,
        *,
        status: str,
        input_kind: str | None,
        pages: int | None,
        issues: list[dict[str, Any]] | None,
        invoice: dict[str, Any] | None,
        model: str | None,
        attempts: int,
        processing_ms: int | None,
    ) -> None:
        await self._session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(
                status=status,
                input_kind=input_kind,
                pages=pages,
                issues=issues,
                invoice=invoice,
                model=model,
                attempts=attempts,
                processing_ms=processing_ms,
            )
        )

    async def add_llm_calls(
        self, document_id: int, now: datetime, calls: Iterable[CallRow]
    ) -> None:
        for call in calls:
            self._session.add(
                LLMCall(
                    document_id=document_id,
                    created_at=now,
                    provider=call.provider,
                    model=call.model,
                    status=call.status,
                    http_status=call.http_status,
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                    thinking_tokens=call.thinking_tokens,
                    latency_ms=call.latency_ms,
                    cost_usd=str(call.cost_usd),
                )
            )
        await self._session.flush()

    async def delete_older_than(self, cutoff: datetime) -> tuple[int, int]:
        """Delete documents created before ``cutoff`` and their calls; return the counts."""
        old_docs = select(Document.id).where(Document.created_at < cutoff)
        calls = await self._session.execute(
            delete(LLMCall).where((LLMCall.created_at < cutoff) | LLMCall.document_id.in_(old_docs))
        )
        docs = await self._session.execute(delete(Document).where(Document.created_at < cutoff))
        return int(docs.rowcount or 0), int(calls.rowcount or 0)

    async def delete_inactive_users(self, cutoff: datetime) -> int:
        """Delete users first seen before ``cutoff`` who have no documents left."""
        has_documents = select(Document.id).where(Document.user_telegram_id == User.telegram_id)
        result = await self._session.execute(
            delete(User).where(User.first_seen_at < cutoff, ~has_documents.exists())
        )
        return int(result.rowcount or 0)

    async def list_user_documents(
        self, user: int, *, since: datetime, statuses: Iterable[str]
    ) -> list[Document]:
        rows = await self._session.execute(
            select(Document)
            .where(
                Document.user_telegram_id == user,
                Document.created_at >= since,
                Document.status.in_(list(statuses)),
            )
            .order_by(Document.id)
        )
        return list(rows.scalars())

    async def status_counts(self, *, since: datetime) -> dict[str, int]:
        """Documents per status created since ``since``."""
        rows = await self._session.execute(
            select(Document.status, func.count(Document.id))
            .where(Document.created_at >= since)
            .group_by(Document.status)
        )
        return {status: int(count) for status, count in rows.all()}

    async def distinct_users(self, *, since: datetime) -> int:
        """Users who sent at least one document since ``since``."""
        query = select(func.count(func.distinct(Document.user_telegram_id))).where(
            Document.created_at >= since
        )
        return int((await self._session.execute(query)).scalar_one())

    async def call_totals(self, *, since: datetime) -> list[tuple[str, int | None, int, int, int]]:
        """(status, http_status, calls, input_tokens, output_tokens) groups since ``since``."""
        rows = await self._session.execute(
            select(
                LLMCall.status,
                LLMCall.http_status,
                func.count(LLMCall.id),
                func.coalesce(func.sum(LLMCall.input_tokens), 0),
                func.coalesce(func.sum(LLMCall.output_tokens), 0),
            )
            .where(LLMCall.created_at >= since)
            .group_by(LLMCall.status, LLMCall.http_status)
        )
        return [(s, h, int(c), int(i), int(o)) for s, h, c, i, o in rows.all()]

    async def get_document(self, document_id: int) -> Document | None:
        return await self._session.get(Document, document_id)

    async def list_documents(self) -> list[Document]:
        rows = await self._session.execute(select(Document).order_by(Document.id))
        return list(rows.scalars())

    async def list_llm_calls(self) -> list[LLMCall]:
        rows = await self._session.execute(select(LLMCall).order_by(LLMCall.id))
        return list(rows.scalars())


class Storage:
    """Owns the async engine; one instance per process."""

    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url)
        if url.startswith("sqlite"):
            event.listen(self.engine.sync_engine, "connect", _sqlite_pragmas)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @classmethod
    def for_path(cls, path: Path) -> "Storage":
        return cls(sqlite_url(path))

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Repository]:
        async with self._sessions() as session, session.begin():
            yield Repository(session)

    async def schema_ready(self) -> bool:
        """True if the Alembic migrations have been applied (checked without Alembic)."""
        async with self.engine.connect() as conn:
            tables = await conn.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        return {"alembic_version", "users", "documents", "llm_calls"} <= tables

    async def dispose(self) -> None:
        await self.engine.dispose()


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    cursor.close()
