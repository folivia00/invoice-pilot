"""Database tables (SQLAlchemy 2 declarative). The schema is created by Alembic only.

No column ever holds document content: not the file bytes, not base64, not the
PDF text layer. ``documents.invoice`` keeps the fields extracted by the model,
which are deleted with the row after the retention period.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Aware UTC datetimes in Python, naive UTC values in SQLite (which has no time zones)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime: pass an aware UTC datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_created_at", "created_at"),
        Index("ix_documents_user_created_at", "user_telegram_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    #: reserved | auto_approved | needs_review | provider_error | rejected | failed
    status: Mapped[str] = mapped_column(String(32))
    input_kind: Mapped[str | None] = mapped_column(String(16), default=None)
    pages: Mapped[int | None] = mapped_column(Integer, default=None)
    #: Validation issues: [{"code", "field", "severity", "message"}].
    issues: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=None)
    #: Extracted invoice fields (Invoice.model_dump(mode="json")); None if extraction failed.
    invoice: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    model: Mapped[str | None] = mapped_column(String(64), default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    processing_ms: Mapped[int | None] = mapped_column(Integer, default=None)


class LLMCall(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("ix_llm_calls_created_at", "created_at"),
        Index("ix_llm_calls_document_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    #: ok | invalid_response | provider_error | timeout (llm.types.CallStatus)
    status: Mapped[str] = mapped_column(String(32))
    http_status: Mapped[int | None] = mapped_column(Integer, default=None)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    thinking_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    #: Exact Decimal as a string (SQLite has no decimal type; never a float).
    cost_usd: Mapped[str] = mapped_column(String(32))
