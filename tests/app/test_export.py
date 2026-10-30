"""Exports: CSV (BOM, columns, injection), XLSX (sheets, numbers), JSON button, ownership."""

import asyncio
import csv
import io
import json
import subprocess
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from helpers_app import NOW, REPO, FakeCallback, FakeClock, FakeMessage, ground_truth, open_storage
from openpyxl import load_workbook

from invoice_pilot.bot.handlers import on_export, on_export_choice, on_json
from invoice_pilot.bot.messages import DOCUMENT_UNAVAILABLE, NOTHING_TO_EXPORT
from invoice_pilot.domain import Invoice
from invoice_pilot.services.export import (
    COLUMNS,
    LINE_COLUMNS,
    ExportService,
    build_csv,
    escape_formula,
)
from invoice_pilot.storage import Storage

OWNER, OTHER = 42, 7


def _invoice(doc_id: str = "inv-001", **overrides: Any) -> dict[str, Any]:
    data = Invoice.model_validate(ground_truth(doc_id)["invoice"]).model_dump(mode="json")
    data.update(overrides)
    return data


async def _seed(
    storage: Storage, rows: list[tuple[int, str, dict[str, Any] | None, int]]
) -> list[int]:
    """rows: (user, status, invoice, age_days) -> document ids."""
    ids = []
    async with storage.transaction() as repo:
        for user in {r[0] for r in rows}:
            await repo.ensure_user(user, NOW - timedelta(days=30))
        for user, status, invoice, age in rows:
            created = NOW - timedelta(days=age)
            doc = await repo.add_document(user=user, now=created, status=status)
            issues = (
                [
                    {
                        "code": "total_mismatch",
                        "field": "total",
                        "severity": "error",
                        "message": "m",
                    },
                    {
                        "code": "tax_id_unverified",
                        "field": "x",
                        "severity": "warning",
                        "message": "w",
                    },
                ]
                if status == "needs_review"
                else []
            )
            await repo.finish_document(
                doc,
                status=status,
                input_kind="text_pdf",
                pages=1,
                issues=issues,
                invoice=invoice,
                model="gemini-3.5-flash-lite",
                attempts=1,
                processing_ms=2000,
            )
            ids.append(doc)
    return ids


SEED = [
    (OWNER, "auto_approved", _invoice("inv-001"), 0),
    (OWNER, "needs_review", _invoice("inv-007"), 1),
    (OWNER, "provider_error", None, 0),  # not exported
    (OWNER, "auto_approved", _invoice("inv-002"), 8),  # past retention
    (OTHER, "auto_approved", _invoice("inv-003"), 0),  # someone else's
]


def _export(db_path: Path, fmt: str, user: int = OWNER, seed: list[Any] = SEED) -> Any:
    async def scenario() -> Any:
        async with open_storage(db_path) as storage:
            await _seed(storage, seed)
            service = ExportService(storage, retention_days=7, clock=FakeClock())
            return await service.export(user, fmt)

    return asyncio.run(scenario())


def test_csv_has_bom_header_and_only_own_recent_final_documents(db_path: Path) -> None:
    file = _export(db_path, "csv")
    assert file.filename == "invoices-20261031-120000.csv"
    assert file.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(file.content.decode("utf-8-sig"))))
    assert rows[0] == list(COLUMNS)
    assert [r[0] for r in rows[1:]] == ["1", "2"]
    first = dict(zip(COLUMNS, rows[1], strict=True))
    assert first["processed_at"] == "2026-10-31 12:00:00"
    assert first["status"] == "auto_approved"
    assert first["supplier"] == "Northwind Supplies Corp."
    assert first["total"] == "3798.41"
    assert first["invoice_date"] == "2025-11-27"
    assert first["line_items"] == "4"
    assert first["iban"] == ""
    second = dict(zip(COLUMNS, rows[2], strict=True))
    assert second["reasons"] == "total_mismatch"  # errors only, warnings are not reasons


def test_xlsx_sheets_columns_and_numbers(db_path: Path) -> None:
    file = _export(db_path, "xlsx")
    assert file.filename.endswith(".xlsx")
    workbook = load_workbook(io.BytesIO(file.content))
    assert workbook.sheetnames == ["Invoices", "Line items"]
    invoices = list(workbook["Invoices"].iter_rows(values_only=True))
    assert list(invoices[0]) == list(COLUMNS)
    assert len(invoices) == 3
    row = dict(zip(COLUMNS, invoices[1], strict=True))
    for column in ("subtotal", "tax_rate", "tax_amount", "total", "id", "line_items"):
        assert isinstance(row[column], int | float), column
    assert row["total"] == pytest.approx(3798.41)
    assert row["invoice_date"].date().isoformat() == "2025-11-27"
    lines = list(workbook["Line items"].iter_rows(values_only=True))
    assert list(lines[0]) == list(LINE_COLUMNS)
    assert lines[1][:3] == (1, 1, "Onsite installation, hours")
    assert all(isinstance(v, int | float) for v in lines[1][3:])
    assert len(lines) == 1 + 4 + 5  # inv-001 has 4 lines, inv-007 has 5


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('=HYPERLINK("http://x")', '\'=HYPERLINK("http://x")'),
        ("+cmd", "'+cmd"),
        ("-2+3", "'-2+3"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tTab", "'\tTab"),
        ("-12.50", "-12.50"),
        ("+7", "+7"),
        ("Acme = Co", "Acme = Co"),
        ("", ""),
    ],
)
def test_escape_formula(value: str, expected: str) -> None:
    assert escape_formula(value) == expected


def test_injection_is_escaped_in_csv_and_xlsx(db_path: Path) -> None:
    evil = _invoice(
        "inv-001",
        supplier={"name": "=1+1", "tax_id": "@x", "address": None},
        invoice_number="-cmd|calc",
    )
    seed = [(OWNER, "auto_approved", evil, 0)]
    csv_file = _export(db_path, "csv", seed=seed)
    row = dict(
        zip(
            COLUMNS,
            list(csv.reader(io.StringIO(csv_file.content.decode("utf-8-sig"))))[1],
            strict=True,
        )
    )
    assert (row["supplier"], row["supplier_tax_id"], row["invoice_number"]) == (
        "'=1+1",
        "'@x",
        "'-cmd|calc",
    )
    assert row["total"] == "3798.41"  # numbers untouched


def test_injection_is_escaped_in_xlsx(db_path: Path) -> None:
    evil = _invoice("inv-001", supplier={"name": "=1+1", "tax_id": None, "address": None})
    file = _export(db_path, "xlsx", seed=[(OWNER, "auto_approved", evil, 0)])
    sheet = load_workbook(io.BytesIO(file.content))["Invoices"]
    cell = sheet.cell(row=2, column=COLUMNS.index("supplier") + 1)
    assert cell.value == "'=1+1"
    assert cell.data_type == "s"  # stored as text, not as a formula


def test_empty_export(db_path: Path) -> None:
    assert _export(db_path, "csv", user=999) is None
    assert build_csv([]).decode("utf-8-sig").strip() == ",".join(COLUMNS)


def _callback(db_path: Path, data: str, user: int) -> FakeCallback:
    callback = FakeCallback(data=data, user_id=user)

    async def scenario() -> None:
        async with open_storage(db_path) as storage:
            await _seed(storage, SEED)
            service = ExportService(storage, retention_days=7, clock=FakeClock())
            handler = on_json if data.startswith("json:") else on_export_choice
            await handler(callback, service)  # type: ignore[arg-type]

    asyncio.run(scenario())
    return callback


def test_export_buttons_send_files_or_nothing_message(db_path: Path) -> None:
    sent = _callback(db_path, "export:xlsx", OWNER)
    ((chat, filename, content),) = sent.bot.sent_documents
    assert chat == OWNER and filename.endswith(".xlsx") and content[:2] == b"PK"
    assert sent.answered == 1
    empty = _callback(db_path, "export:csv", 999)
    assert empty.bot.sent_messages == [(999, NOTHING_TO_EXPORT)]
    assert empty.bot.sent_documents == []


def test_export_command_offers_both_formats() -> None:
    message = FakeMessage(text="/export")
    asyncio.run(on_export(message, retention_days=7))  # type: ignore[arg-type]
    (text, kwargs) = message.answers[0]
    assert text == "Export your documents of the last 7 days:"
    buttons = kwargs["reply_markup"].inline_keyboard[0]
    assert [(b.text, b.callback_data) for b in buttons] == [
        ("CSV", "export:csv"),
        ("XLSX", "export:xlsx"),
    ]


def test_json_button_own_document(db_path: Path) -> None:
    callback = _callback(db_path, "json:2", OWNER)
    ((_, filename, content),) = callback.bot.sent_documents
    assert filename == "invoice-2.json"
    payload = json.loads(content)
    assert payload["id"] == 2 and payload["status"] == "needs_review"
    assert payload["model"] == "gemini-3.5-flash-lite"
    assert [i["code"] for i in payload["issues"]] == ["total_mismatch", "tax_id_unverified"]
    assert Decimal(payload["invoice"]["total"]) == Decimal("4241.62")


@pytest.mark.parametrize(
    "data",
    [
        "json:5",  # someone else's document
        "json:3",  # own, but a provider error (not a result)
        "json:999",  # deleted / never existed
        "json:abc",  # malformed
    ],
)
def test_json_button_refuses_other_users_and_missing_documents(db_path: Path, data: str) -> None:
    callback = _callback(db_path, data, OWNER)
    assert callback.bot.sent_documents == []
    assert callback.bot.sent_messages == [(OWNER, DOCUMENT_UNAVAILABLE)]
    assert DOCUMENT_UNAVAILABLE == "This document is no longer available."


def test_openpyxl_is_loaded_lazily_only_on_xlsx_export() -> None:
    code = (
        "import sys, invoice_pilot.bot, invoice_pilot.bot.app\n"
        "before = 'openpyxl' in sys.modules\n"
        "from invoice_pilot.services.export import build_csv, build_xlsx\n"
        "build_csv([])\n"
        "after_csv = 'openpyxl' in sys.modules\n"
        "build_xlsx([])\n"
        "print(before, after_csv, 'openpyxl' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=REPO
    )
    assert result.stdout.split() == ["False", "False", "True"]
