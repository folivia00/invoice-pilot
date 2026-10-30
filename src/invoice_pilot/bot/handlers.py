"""aiogram handlers: read the update, call the service, send the reply.

No limits, database, rights or LLM logic here. The handlers use only a few
attributes of ``Message`` / ``CallbackQuery`` (``from_user``, ``document``,
``photo``, ``bot``, ``answer``, ``data``, ``message``), so tests call them
directly with lightweight fakes.
"""

import io
import logging
from typing import Any, Final

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PhotoSize,
)

from invoice_pilot.bot.card import render_result
from invoice_pilot.bot.messages import (
    DOCUMENT_UNAVAILABLE,
    EXPORT_PROMPT,
    HINT,
    NOTHING_TO_EXPORT,
    start_text,
    stats_text,
)
from invoice_pilot.services.export import ExportService
from invoice_pilot.services.processing import DocumentProcessor, ResultKind, UploadMeta
from invoice_pilot.services.stats import StatsService

logger = logging.getLogger(__name__)

HTML: Any = "HTML"
EXPORT_PREFIX: Final = "export:"
JSON_PREFIX: Final = "json:"


def export_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="CSV", callback_data=f"{EXPORT_PREFIX}csv"),
                InlineKeyboardButton(text="XLSX", callback_data=f"{EXPORT_PREFIX}xlsx"),
            ]
        ]
    )


def json_keyboard(document_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="JSON", callback_data=f"{JSON_PREFIX}{document_id}")]
        ]
    )


def _largest(photos: list[PhotoSize]) -> PhotoSize:
    return max(photos, key=lambda p: (p.width * p.height, p.file_size or 0))


async def on_start(message: Message, processor: DocumentProcessor, retention_days: int) -> None:
    user = message.from_user
    if user is None:
        return
    remaining = await processor.remaining_today(user.id)
    await message.answer(
        start_text(
            retention_days=retention_days,
            remaining_today=remaining,
            daily_limit=processor.user_daily_docs,
        ),
        parse_mode=HTML,
    )


async def _process(
    message: Message, processor: DocumentProcessor, file_id: str, meta: UploadMeta
) -> None:
    user = message.from_user
    if user is None:
        return
    bot = message.bot
    assert bot is not None

    async def download() -> bytes:
        buffer = io.BytesIO()
        await bot.download(file_id, destination=buffer)
        data = buffer.getvalue()
        buffer.close()
        return data

    async def notify(text: str) -> None:
        await message.answer(text)

    result = await processor.process(user.id, meta, download, notify)
    markup = None
    if result.kind is ResultKind.DONE and result.document_id is not None:
        markup = json_keyboard(result.document_id)
    await message.answer(render_result(result), parse_mode=HTML, reply_markup=markup)


async def on_document(message: Message, processor: DocumentProcessor) -> None:
    document = message.document
    assert document is not None
    meta = UploadMeta(
        kind="document",
        mime_type=document.mime_type,
        file_name=document.file_name,
        file_size=document.file_size,
    )
    await _process(message, processor, document.file_id, meta)


async def on_photo(message: Message, processor: DocumentProcessor) -> None:
    assert message.photo
    photo = _largest(list(message.photo))
    meta = UploadMeta(
        kind="photo", mime_type="image/jpeg", file_name=None, file_size=photo.file_size
    )
    await _process(message, processor, photo.file_id, meta)


async def on_export(message: Message, retention_days: int) -> None:
    await message.answer(EXPORT_PROMPT.format(days=retention_days), reply_markup=export_keyboard())


def _chat_id(callback: CallbackQuery) -> int:
    if callback.message is not None:
        return callback.message.chat.id
    return callback.from_user.id


async def _send_file(callback: CallbackQuery, filename: str, content: bytes) -> None:
    bot = callback.bot
    assert bot is not None
    await bot.send_document(_chat_id(callback), BufferedInputFile(content, filename=filename))


async def _send_text(callback: CallbackQuery, text: str) -> None:
    bot = callback.bot
    assert bot is not None
    await bot.send_message(_chat_id(callback), text)


async def on_export_choice(callback: CallbackQuery, exports: ExportService) -> None:
    fmt = (callback.data or "").removeprefix(EXPORT_PREFIX)
    if fmt not in ("csv", "xlsx"):
        await callback.answer()
        return
    file = await exports.export(callback.from_user.id, fmt)
    if file is None:
        await _send_text(callback, NOTHING_TO_EXPORT)
    else:
        await _send_file(callback, file.filename, file.content)
        logger.info("export sent: format=%s bytes=%d", fmt, len(file.content))
    await callback.answer()


async def on_json(callback: CallbackQuery, exports: ExportService) -> None:
    raw = (callback.data or "").removeprefix(JSON_PREFIX)
    file = None
    if raw.isdigit():
        file = await exports.document_json(callback.from_user.id, int(raw))
    if file is None:
        await _send_text(callback, DOCUMENT_UNAVAILABLE)
    else:
        await _send_file(callback, file.filename, file.content)
    await callback.answer()


async def on_stats(message: Message, stats: StatsService) -> None:
    user = message.from_user
    snapshot = await stats.snapshot(user.id) if user is not None else None
    if snapshot is None:
        await message.answer(HINT)  # same reply as any unknown command
        return
    await message.answer(stats_text(snapshot), parse_mode=HTML)


async def on_other(message: Message) -> None:
    await message.answer(HINT)


def build_router() -> Router:
    router = Router(name="invoice_pilot")
    router.message.register(on_start, CommandStart())
    router.message.register(on_export, Command("export"))
    router.message.register(on_stats, Command("stats"))
    router.message.register(on_document, F.document)
    router.message.register(on_photo, F.photo)
    router.message.register(on_other)
    router.callback_query.register(on_export_choice, F.data.startswith(EXPORT_PREFIX))
    router.callback_query.register(on_json, F.data.startswith(JSON_PREFIX))
    return router
