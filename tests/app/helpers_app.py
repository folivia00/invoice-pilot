"""Shared helpers for storage, service and bot tests (no network, no Telegram)."""

import asyncio
import json
import re
import shutil
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from alembic import command
from alembic.config import Config

from invoice_pilot.extraction.prepare import InputLimits
from invoice_pilot.llm.types import DocumentInput, ProviderResponse, UsageRecord
from invoice_pilot.services.limits import DailyLimits, LockFactory
from invoice_pilot.services.processing import DocumentProcessor, ProcessingQueue
from invoice_pilot.storage import Storage, sqlite_url

REPO = Path(__file__).resolve().parents[2]
DATASET = REPO / "eval" / "dataset"
MODEL = "gemini-3.5-flash-lite"
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def migrate(db_path: Path) -> None:
    """Apply the Alembic migrations to a fresh SQLite file (as `alembic upgrade head`)."""
    cfg = Config(str(REPO / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", sqlite_url(db_path, driver=""))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


def copy_db(template: Path, target: Path) -> Path:
    shutil.copyfile(template, target)
    return target


@asynccontextmanager
async def open_storage(path: Path) -> AsyncIterator[Storage]:
    storage = Storage.for_path(path)
    try:
        yield storage
    finally:
        await storage.dispose()


class FakeClock:
    """Wall clock and monotonic clock that only move when told (or when sleep is awaited)."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now
        self.mono = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.mono += seconds

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)
        await asyncio.sleep(0)


# --- model answers ---------------------------------------------------------------------

_NUMERIC = {"quantity", "unit_price", "amount", "subtotal", "tax_rate", "tax_amount", "total"}


def answer_json(invoice: dict[str, Any]) -> str:
    """Invoice dict (Decimal strings) -> model answer text with JSON numbers."""

    def convert(node: Any, key: str | None = None) -> Any:
        if isinstance(node, dict):
            return {k: convert(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [convert(v, key) for v in node]
        if key in _NUMERIC and isinstance(node, str):
            return Decimal(node)
        return node

    text = json.dumps(convert(invoice), default=lambda d: f"@@RAW:{d}@@", ensure_ascii=False)
    return re.sub(r'"@@RAW:([^"]*)@@"', r"\1", text)


def ground_truth(doc_id: str) -> dict[str, Any]:
    return json.loads((DATASET / "ground_truth" / f"{doc_id}.json").read_text(encoding="utf-8"))


def dataset_bytes(doc_id: str) -> bytes:
    return (DATASET / ground_truth(doc_id)["file"]).read_bytes()


def truth_answer(doc_id: str) -> str:
    return answer_json(ground_truth(doc_id)["invoice"])


def usage(status: str = "ok", http_status: int | None = 200) -> UsageRecord:
    tokens_in, tokens_out = (1000, 500) if status == "ok" else (0, 0)
    return UsageRecord("gemini", MODEL, status, tokens_in, tokens_out, 0, 1500, http_status)  # type: ignore[arg-type]


class FakeProvider:
    """Answers from a list (texts or exceptions); can hold every call on a gate."""

    name = "gemini"
    model = MODEL
    params: dict[str, str] = {}

    def __init__(self, *answers: str | Exception, gate: asyncio.Event | None = None) -> None:
        self.answers = list(answers)
        self.gate = gate
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.started = asyncio.Event()

    async def extract(self, doc: DocumentInput) -> ProviderResponse:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            if self.gate is not None:
                await self.gate.wait()
            answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
            if isinstance(answer, Exception):
                raise answer
            record = usage()
            return ProviderResponse(raw_text=answer, usage=record, attempts=(record,))
        finally:
            self.active -= 1


def make_processor(
    storage: Storage,
    provider: Any,
    clock: FakeClock,
    *,
    user_daily: int = 5,
    global_daily: int = 300,
    budget: Decimal = Decimal("0.50"),
    concurrency: int = 1,
    lock_factory: LockFactory = asyncio.Lock,
    max_bytes: int = 5 * 1024 * 1024,
    max_pages: int = 5,
) -> DocumentProcessor:
    limits = DailyLimits(
        storage,
        user_daily_docs=user_daily,
        global_daily_docs=global_daily,
        global_daily_budget_usd=budget,
        clock=clock,
        lock_factory=lock_factory,
    )
    return DocumentProcessor(
        storage=storage,
        limits=limits,
        provider=provider,
        queue=ProcessingQueue(concurrency),
        input_limits=InputLimits(max_bytes=max_bytes, max_pages=max_pages),
        clock=clock,
        monotonic=clock.monotonic,
    )


# --- fake aiogram objects --------------------------------------------------------------


class FakeBot:
    """Serves file bytes by file_id; records downloads."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {}
        self.downloads: list[str] = []

    async def download(self, file_id: str, destination: Any) -> Any:
        self.downloads.append(file_id)
        destination.write(self.files[file_id])
        destination.seek(0)
        return destination


@dataclass
class FakeMessage:
    """The attributes of aiogram.types.Message that the handlers use."""

    user_id: int = 42
    bot: FakeBot = field(default_factory=FakeBot)
    document: Any = None
    photo: Any = None
    text: str | None = None
    answers: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @property
    def from_user(self) -> Any:
        return SimpleNamespace(id=self.user_id)

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))

    @property
    def texts(self) -> list[str]:
        return [text for text, _ in self.answers]


def document_message(
    data: bytes,
    *,
    file_name: str,
    mime_type: str | None,
    user_id: int = 42,
    file_size: int | None = None,
) -> FakeMessage:
    file_id = f"file-{file_name}"
    doc = SimpleNamespace(
        file_id=file_id,
        file_name=file_name,
        mime_type=mime_type,
        file_size=len(data) if file_size is None else file_size,
    )
    return FakeMessage(user_id=user_id, bot=FakeBot({file_id: data}), document=doc)


def photo_message(data: bytes, *, user_id: int = 42) -> FakeMessage:
    sizes = [
        SimpleNamespace(file_id="small", width=90, height=120, file_size=2_000),
        SimpleNamespace(file_id="large", width=960, height=1280, file_size=len(data)),
        SimpleNamespace(file_id="medium", width=320, height=427, file_size=20_000),
    ]
    return FakeMessage(
        user_id=user_id, bot=FakeBot({"large": data, "small": b"x", "medium": b"y"}), photo=sizes
    )


Runner = Callable[[Any], Any]
