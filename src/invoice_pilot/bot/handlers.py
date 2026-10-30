"""aiogram handlers: read the update, call the service, send the reply.

No limits, database or LLM logic here. The handlers use only a few attributes
of ``Message`` (``from_user``, ``document``, ``photo``, ``bot``, ``answer``), so
tests call them directly with lightweight fakes.
"""

import io
from typing import Any

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message, PhotoSize

from invoice_pilot.bot.card import render_result
from invoice_pilot.bot.messages import HINT, start_text
from invoice_pilot.services.processing import DocumentProcessor, UploadMeta

HTML: Any = "HTML"


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
    await message.answer(render_result(result), parse_mode=HTML)


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


async def on_other(message: Message) -> None:
    await message.answer(HINT)


def build_router() -> Router:
    router = Router(name="invoice_pilot")
    router.message.register(on_start, CommandStart())
    router.message.register(on_document, F.document)
    router.message.register(on_photo, F.photo)
    router.message.register(on_other)
    return router
