"""Initial schema: users, documents, llm_calls.

Revision ID: 0001
Revises:
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_telegram_id",
            sa.BigInteger(),
            sa.ForeignKey("users.telegram_id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_kind", sa.String(16), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.Column("issues", sa.JSON(), nullable=True),
        sa.Column("invoice", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("processing_ms", sa.Integer(), nullable=True),
    )
    op.create_index("ix_documents_created_at", "documents", ["created_at"])
    op.create_index("ix_documents_user_created_at", "documents", ["user_telegram_id", "created_at"])
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("thinking_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.String(32), nullable=False),
    )
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])
    op.create_index("ix_llm_calls_document_id", "llm_calls", ["document_id"])


def downgrade() -> None:
    op.drop_table("llm_calls")
    op.drop_table("documents")
    op.drop_table("users")
