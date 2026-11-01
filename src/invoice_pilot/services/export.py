"""Exports of a user's own documents: CSV, XLSX, and the JSON of one document.

Files are built in memory and returned as bytes; nothing is written to disk.
Only documents of the requesting user, within the retention period, with a
final status (auto_approved, needs_review) are exported. ``openpyxl`` is
imported inside ``build_xlsx`` only, so the bot process loads it on first use.

Spreadsheet formula injection: a text value that starts with ``=``, ``+``,
``-``, ``@``, tab or carriage return (and is not a plain number) gets a leading
apostrophe (OWASP "CSV Injection" rule) in both CSV and XLSX.
"""

import csv
import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from invoice_pilot.services.limits import STATUS_AUTO_APPROVED, STATUS_NEEDS_REVIEW
from invoice_pilot.storage import Document, Storage

Clock = Callable[[], datetime]

EXPORTED_STATUSES: Final[tuple[str, ...]] = (STATUS_AUTO_APPROVED, STATUS_NEEDS_REVIEW)

COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "processed_at",
    "status",
    "reasons",
    "supplier",
    "supplier_tax_id",
    "buyer",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "subtotal",
    "tax_rate",
    "tax_amount",
    "total",
    "iban",
    "bic",
    "line_items",
)
MONEY_COLUMNS: Final[frozenset[str]] = frozenset({"subtotal", "tax_rate", "tax_amount", "total"})
DATE_COLUMNS: Final[frozenset[str]] = frozenset({"invoice_date", "due_date"})
LINE_COLUMNS: Final[tuple[str, ...]] = (
    "document_id",
    "line",
    "description",
    "quantity",
    "unit_price",
    "amount",
)

#: XLSX column widths (characters): long enough for the header, dates, IDs and amounts
#: up to 999,999,999,999.99, so no cell shows "#######"; long names wrap at the edge.
XLSX_WIDTHS: Final[dict[str, float]] = {
    "id": 8,
    "processed_at": 20,
    "status": 15,
    "reasons": 30,
    "supplier": 32,
    "supplier_tax_id": 18,
    "buyer": 32,
    "invoice_number": 18,
    "invoice_date": 13,
    "due_date": 13,
    "currency": 10,
    "subtotal": 20,
    "tax_rate": 10,
    "tax_amount": 20,
    "total": 20,
    "iban": 34,
    "bic": 13,
    "line_items": 11,
}
XLSX_LINE_WIDTHS: Final[dict[str, float]] = {
    "document_id": 13,
    "line": 6,
    "description": 40,
    "quantity": 11,
    "unit_price": 20,
    "amount": 20,
}
#: Amounts keep their exact value; only the display has two decimals and thousands separators.
XLSX_AMOUNT_FORMAT: Final = "#,##0.00"
XLSX_AMOUNT_COLUMNS: Final[frozenset[str]] = frozenset(
    {"subtotal", "tax_amount", "total", "unit_price", "amount"}
)
_XLSX_FORMATS: Final[dict[str, str]] = {
    **{column: "yyyy-mm-dd" for column in DATE_COLUMNS},
    "processed_at": "yyyy-mm-dd hh:mm:ss",
    **{column: XLSX_AMOUNT_FORMAT for column in XLSX_AMOUNT_COLUMNS},
}

_FORMULA_START: Final = ("=", "+", "-", "@", "\t", "\r")
_PLAIN_NUMBER: Final = re.compile(r"^[+-]?\d+(\.\d+)?$")


def escape_formula(value: str) -> str:
    """Prefix ``'`` when a spreadsheet could read ``value`` as a formula."""
    if value.startswith(_FORMULA_START) and not _PLAIN_NUMBER.match(value):
        return "'" + value
    return value


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _reasons(doc: Document) -> str:
    codes = [i["code"] for i in (doc.issues or []) if i.get("severity") == "error"]
    return ";".join(dict.fromkeys(codes))


def document_row(doc: Document) -> dict[str, Any]:
    """Typed values of one document (Decimal, date, str, int or None)."""
    inv = doc.invoice or {}
    supplier = inv.get("supplier") or {}
    buyer = inv.get("buyer") or {}
    return {
        "id": doc.id,
        "processed_at": doc.created_at.replace(microsecond=0, tzinfo=None),
        "status": doc.status,
        "reasons": _reasons(doc),
        "supplier": supplier.get("name"),
        "supplier_tax_id": supplier.get("tax_id"),
        "buyer": buyer.get("name"),
        "invoice_number": inv.get("invoice_number"),
        "invoice_date": _date(inv.get("invoice_date")),
        "due_date": _date(inv.get("due_date")),
        "currency": inv.get("currency"),
        "subtotal": _decimal(inv.get("subtotal")),
        "tax_rate": _decimal(inv.get("tax_rate")),
        "tax_amount": _decimal(inv.get("tax_amount")),
        "total": _decimal(inv.get("total")),
        "iban": inv.get("iban"),
        "bic": inv.get("bic"),
        "line_items": len(inv.get("line_items") or []),
    }


def line_rows(doc: Document) -> list[dict[str, Any]]:
    items = (doc.invoice or {}).get("line_items") or []
    return [
        {
            "document_id": doc.id,
            "line": n,
            "description": item.get("description"),
            "quantity": _decimal(item.get("quantity")),
            "unit_price": _decimal(item.get("unit_price")),
            "amount": _decimal(item.get("amount")),
        }
        for n, item in enumerate(items, start=1)
    ]


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, str):
        return escape_formula(value)
    return str(value)


def build_csv(docs: list[Document]) -> bytes:
    """UTF-8 with BOM (Excel opens it correctly), comma separated, one row per document."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for doc in docs:
        row = document_row(doc)
        writer.writerow([_csv_cell(row[c]) for c in COLUMNS])
    return buffer.getvalue().encode("utf-8-sig")


def _xlsx_cell(value: Any) -> Any:
    if isinstance(value, str):
        return escape_formula(value)
    return value


def _format_sheet(sheet: Any, columns: tuple[str, ...], widths: dict[str, float]) -> None:
    from openpyxl.utils import get_column_letter

    for index, column in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = widths[column]
    for row in sheet.iter_rows(min_row=2):
        for cell, column in zip(row, columns, strict=True):
            if column in _XLSX_FORMATS and cell.value is not None:
                cell.number_format = _XLSX_FORMATS[column]


def build_xlsx(docs: list[Document]) -> bytes:
    """Workbook with sheets "Invoices" and "Line items"; amounts are numbers."""
    from openpyxl import Workbook  # lazy: loaded only when a user exports XLSX

    workbook = Workbook()
    invoices = workbook.active
    assert invoices is not None
    invoices.title = "Invoices"
    invoices.append(list(COLUMNS))
    lines = workbook.create_sheet("Line items")
    lines.append(list(LINE_COLUMNS))
    for doc in docs:
        row = document_row(doc)
        invoices.append([_xlsx_cell(row[c]) for c in COLUMNS])
        for line in line_rows(doc):
            lines.append([_xlsx_cell(line[c]) for c in LINE_COLUMNS])
    _format_sheet(invoices, COLUMNS, XLSX_WIDTHS)
    _format_sheet(lines, LINE_COLUMNS, XLSX_LINE_WIDTHS)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_document_json(doc: Document) -> bytes:
    payload = {
        "id": doc.id,
        "processed_at": doc.created_at.isoformat(timespec="seconds"),
        "status": doc.status,
        "model": doc.model,
        "issues": doc.issues or [],
        "invoice": doc.invoice,
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


@dataclass(frozen=True, slots=True)
class ExportFile:
    filename: str
    content: bytes
    rows: int = 1  # documents in the file


class ExportService:
    """Builds export files for the requesting user only."""

    def __init__(self, storage: Storage, *, retention_days: int, clock: Clock) -> None:
        self._storage = storage
        self._retention_days = retention_days
        self._clock = clock

    async def _documents(self, user_id: int) -> list[Document]:
        since = self._clock() - timedelta(days=self._retention_days)
        async with self._storage.transaction() as repo:
            return await repo.list_user_documents(user_id, since=since, statuses=EXPORTED_STATUSES)

    async def export(self, user_id: int, fmt: str) -> ExportFile | None:
        """CSV or XLSX of the user's documents; None if there is nothing to export."""
        if fmt not in ("csv", "xlsx"):
            raise ValueError(f"unknown export format {fmt!r}")
        docs = await self._documents(user_id)
        if not docs:
            return None
        stamp = self._clock().strftime("%Y%m%d-%H%M%S")
        content = build_csv(docs) if fmt == "csv" else build_xlsx(docs)
        return ExportFile(f"invoices-{stamp}.{fmt}", content, rows=len(docs))

    async def document_json(self, user_id: int, document_id: int) -> ExportFile | None:
        """JSON of one document if it exists, belongs to ``user_id`` and is exportable."""
        async with self._storage.transaction() as repo:
            doc = await repo.get_document(document_id)
        if doc is None or doc.user_telegram_id != user_id or doc.status not in EXPORTED_STATUSES:
            return None
        return ExportFile(f"invoice-{doc.id}.json", build_document_json(doc))
