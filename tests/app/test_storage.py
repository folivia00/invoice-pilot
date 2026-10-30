"""Storage: Alembic migration on a clean database, repository CRUD, no document content."""

import asyncio
import base64
import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from helpers_app import (
    NOW,
    REPO,
    FakeClock,
    FakeProvider,
    dataset_bytes,
    make_processor,
    migrate,
    open_storage,
    truth_answer,
)

from invoice_pilot.extraction.prepare import prepare
from invoice_pilot.services.processing import ResultKind, UploadMeta
from invoice_pilot.storage import CallRow, sqlite_url
from invoice_pilot.storage.tables import Base


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def test_upgrade_head_on_clean_database(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    migrate(path)
    assert {"alembic_version", "users", "documents", "llm_calls"} <= _tables(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("0001",)


def test_downgrade_to_base_drops_tables(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    migrate(path)
    cfg = Config(str(REPO / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", sqlite_url(path, driver=""))
    cfg.attributes["configure_logger"] = False
    command.downgrade(cfg, "base")
    assert not {"users", "documents", "llm_calls"} & _tables(path)


def test_migration_matches_models(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        for table in Base.metadata.sorted_tables:
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table.name})")}
            assert columns == {c.name for c in table.columns}, table.name


def test_schema_ready(db_path: Path, tmp_path: Path) -> None:
    async def scenario() -> tuple[bool, bool]:
        async with open_storage(db_path) as migrated, open_storage(tmp_path / "empty.db") as empty:
            return await migrated.schema_ready(), await empty.schema_ready()

    assert asyncio.run(scenario()) == (True, False)


def test_repository_crud(db_path: Path) -> None:
    async def scenario() -> None:
        async with open_storage(db_path) as storage:
            async with storage.transaction() as repo:
                await repo.ensure_user(7, NOW)
                await repo.ensure_user(7, NOW + timedelta(hours=1))  # idempotent
                doc_id = await repo.add_document(user=7, now=NOW, status="reserved")
                await repo.add_llm_calls(
                    doc_id,
                    NOW,
                    [
                        CallRow("gemini", "m", "provider_error", 429, 0, 0, 0, 100, Decimal("0")),
                        CallRow("gemini", "m", "ok", 200, 1000, 500, 0, 2000, Decimal("0.00155")),
                    ],
                )
            async with storage.transaction() as repo:
                await repo.finish_document(
                    doc_id,
                    status="auto_approved",
                    input_kind="text_pdf",
                    pages=1,
                    issues=[],
                    invoice={"total": "10.00"},
                    model="m",
                    attempts=1,
                    processing_ms=2100,
                )
                assert await repo.count_documents(since=NOW, statuses=["auto_approved"]) == 1
                assert await repo.count_documents(since=NOW, statuses=["reserved"]) == 0
                assert (
                    await repo.count_documents(since=NOW, statuses=["auto_approved"], user=8) == 0
                )
                assert await repo.spent_since(NOW) == Decimal("0.00155")
                doc = await repo.get_document(doc_id)
                assert doc is not None
                assert doc.created_at == NOW and doc.created_at.tzinfo is not None
                assert doc.invoice == {"total": "10.00"}
                calls = await repo.list_llm_calls()
                assert [c.http_status for c in calls] == [429, 200]
                assert calls[1].cost_usd == "0.00155"

    asyncio.run(scenario())


def test_naive_datetime_is_rejected(db_path: Path) -> None:
    async def scenario() -> None:
        async with open_storage(db_path) as storage:
            async with storage.transaction() as repo:
                await repo.add_document(user=1, now=NOW.replace(tzinfo=None), status="reserved")

    with pytest.raises(Exception, match="naive datetime"):
        asyncio.run(scenario())


@pytest.mark.parametrize("doc_id", ["inv-001", "inv-003", "inv-005"])
def test_document_content_is_never_stored(db_path: Path, clock: FakeClock, doc_id: str) -> None:
    data = dataset_bytes(doc_id)
    prepared = prepare(data)
    probes = [data[len(data) // 2 : len(data) // 2 + 48]]
    probes.append(base64.b64encode(data[300:348]))
    if prepared.input.text:
        probes.append(prepared.input.text.encode("utf-8"))

    async def scenario() -> None:
        async with open_storage(db_path) as storage:
            processor = make_processor(storage, FakeProvider(truth_answer(doc_id)), clock)
            meta = UploadMeta("document", prepared.mime, "invoice", len(data))

            async def download() -> bytes:
                return data

            async def notify(text: str) -> None:
                pass

            result = await processor.process(1, meta, download, notify)
            assert result.kind is ResultKind.DONE

    asyncio.run(scenario())
    stored = b"".join(p.read_bytes() for p in db_path.parent.iterdir() if p.is_file())
    assert stored, "database files must exist"
    for probe in probes:
        assert probe not in stored
