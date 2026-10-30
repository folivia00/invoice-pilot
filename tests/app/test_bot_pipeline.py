"""Bot pipeline end to end: handler -> service -> fake provider -> card.

Handlers are called directly with fake ``Message`` objects (only the attributes
the handlers read), so no Telegram, no network and no aiogram dispatcher is
involved; the router test checks the registration separately.
"""

import asyncio
import io
from pathlib import Path

from helpers_app import (
    FakeClock,
    FakeMessage,
    FakeProvider,
    dataset_bytes,
    document_message,
    make_processor,
    open_storage,
    photo_message,
    truth_answer,
)
from pypdf import PdfWriter

from invoice_pilot.bot.card import STATUS_APPROVED, STATUS_REVIEW
from invoice_pilot.bot.handlers import build_router, on_document, on_other, on_photo, on_start
from invoice_pilot.bot.messages import HINT
from invoice_pilot.services.processing import MSG_UNSUPPORTED


def _run_document(
    db_path: Path, clock: FakeClock, message: FakeMessage, provider: FakeProvider, **kw: int
) -> FakeMessage:
    async def scenario() -> None:
        async with open_storage(db_path) as storage:
            processor = make_processor(storage, provider, clock, **kw)
            if message.photo is not None:
                await on_photo(message, processor)  # type: ignore[arg-type]
            else:
                await on_document(message, processor)  # type: ignore[arg-type]

    asyncio.run(scenario())
    return message


def test_text_pdf_is_approved(db_path: Path, clock: FakeClock) -> None:
    message = document_message(
        dataset_bytes("inv-001"), file_name="inv-001.pdf", mime_type="application/pdf"
    )
    provider = FakeProvider(truth_answer("inv-001"))
    _run_document(db_path, clock, message, provider)
    (card,) = message.texts
    assert card.startswith(STATUS_APPROVED)
    assert "Northwind Supplies Corp." in card
    assert "<b>Total:</b> 3798.41 USD" in card
    assert "gemini-3.5-flash-lite" in card
    assert message.answers[0][1] == {"parse_mode": "HTML"}


def test_scan_pdf_is_approved(db_path: Path, clock: FakeClock) -> None:
    message = document_message(
        dataset_bytes("inv-003"), file_name="inv-003.pdf", mime_type="application/pdf"
    )
    _run_document(db_path, clock, message, FakeProvider(truth_answer("inv-003")))
    assert message.texts[0].startswith(STATUS_APPROVED)
    assert "FR327993656548CAAAC0ELHEL45" in message.texts[0]


def test_photo_sent_as_document_is_approved(db_path: Path, clock: FakeClock) -> None:
    message = document_message(
        dataset_bytes("inv-005"), file_name="inv-005.jpg", mime_type="image/jpeg"
    )
    _run_document(db_path, clock, message, FakeProvider(truth_answer("inv-005")))
    assert message.texts[0].startswith(STATUS_APPROVED)


def test_compressed_photo_uses_the_largest_size(db_path: Path, clock: FakeClock) -> None:
    message = photo_message(dataset_bytes("inv-005"))
    _run_document(db_path, clock, message, FakeProvider(truth_answer("inv-005")))
    assert message.bot.downloads == ["large"]
    assert message.texts[0].startswith(STATUS_APPROVED)


def test_corrupted_invoice_needs_review_with_reason(db_path: Path, clock: FakeClock) -> None:
    message = document_message(
        dataset_bytes("inv-007"), file_name="inv-007.pdf", mime_type="application/pdf"
    )
    _run_document(db_path, clock, message, FakeProvider(truth_answer("inv-007")))
    card = message.texts[0]
    assert card.startswith(STATUS_REVIEW)
    assert "<b>Reasons:</b>" in card
    assert "does not equal subtotal + tax" in card  # the total_mismatch issue message


def test_unsupported_file_is_refused_before_download(db_path: Path, clock: FakeClock) -> None:
    message = document_message(b"plain text\n", file_name="notes.txt", mime_type="text/plain")
    provider = FakeProvider(truth_answer("inv-001"))
    _run_document(db_path, clock, message, provider)
    assert message.texts == [MSG_UNSUPPORTED]
    assert message.bot.downloads == []
    assert provider.calls == 0


def test_disguised_file_is_refused_after_download_and_not_charged(
    db_path: Path, clock: FakeClock
) -> None:
    message = document_message(b"not a pdf at all", file_name="x.pdf", mime_type="application/pdf")
    provider = FakeProvider(truth_answer("inv-001"))
    _run_document(db_path, clock, message, provider, user_daily=1)
    assert message.texts == [
        "Cannot process this file: unsupported file type: send a PDF, JPEG or PNG file."
    ]
    assert provider.calls == 0


def test_too_large_file_is_refused_before_download(db_path: Path, clock: FakeClock) -> None:
    message = document_message(
        b"%PDF-", file_name="big.pdf", mime_type="application/pdf", file_size=6 * 1024 * 1024
    )
    provider = FakeProvider(truth_answer("inv-001"))
    _run_document(db_path, clock, message, provider)
    assert message.texts == ["The file is too large (6.0 MB). The limit is 5 MB."]
    assert message.bot.downloads == []
    assert provider.calls == 0


def test_file_over_telegram_download_limit_is_refused(db_path: Path, clock: FakeClock) -> None:
    message = document_message(
        b"%PDF-", file_name="huge.pdf", mime_type="application/pdf", file_size=25 * 1024 * 1024
    )
    provider = FakeProvider(truth_answer("inv-001"))
    _run_document(db_path, clock, message, provider, max_bytes=50 * 1024 * 1024)
    assert message.texts == ["The file is too large (25.0 MB). The limit is 20 MB."]
    assert message.bot.downloads == []


def _pdf_with_pages(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_six_page_pdf_is_refused_and_not_charged(db_path: Path, clock: FakeClock) -> None:
    message = document_message(_pdf_with_pages(6), file_name="six.pdf", mime_type="application/pdf")
    provider = FakeProvider(truth_answer("inv-001"))

    async def scenario() -> int:
        async with open_storage(db_path) as storage:
            processor = make_processor(storage, provider, clock, user_daily=1)
            await on_document(message, processor)  # type: ignore[arg-type]
            return await processor.remaining_today(42)

    remaining = asyncio.run(scenario())
    assert message.texts == ["Cannot process this file: the PDF has 6 pages, the limit is 5."]
    assert provider.calls == 0
    assert remaining == 1


def test_start_shows_disclaimer_and_remaining(db_path: Path, clock: FakeClock) -> None:
    message = FakeMessage(text="/start")

    async def scenario() -> None:
        async with open_storage(db_path) as storage:
            processor = make_processor(storage, FakeProvider("{}"), clock, user_daily=5)
            await on_start(message, processor, retention_days=7)  # type: ignore[arg-type]

    asyncio.run(scenario())
    (text,) = message.texts
    assert "Send a PDF, scan or photo of an invoice" in text
    assert (
        "Demo. Do not upload real or confidential documents. Files are processed in memory "
        "and never stored; extracted fields are deleted after 7 days. This demo uses the Gemini "
        "API free tier, where Google may use inputs to improve its products."
    ) in text
    assert "Today you can process 5 more of 5 documents" in text


def test_other_messages_get_a_hint() -> None:
    message = FakeMessage(text="hello")
    asyncio.run(on_other(message))  # type: ignore[arg-type]
    assert message.texts == [HINT]


def test_router_registers_the_handlers() -> None:
    router = build_router()
    callbacks = [handler.callback for handler in router.message.handlers]
    assert callbacks == [on_start, on_document, on_photo, on_other]
