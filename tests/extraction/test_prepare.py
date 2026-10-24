"""Input preparation: type sniffing, limits, text layer detection on the whole eval set."""

import io
import json
from pathlib import Path
from typing import Any

import pytest

from invoice_pilot.extraction.prepare import (
    TEXT_LAYER_MIN_CHARS,
    FileTooLargeError,
    InputLimits,
    TooManyPagesError,
    UnreadablePdfError,
    UnsupportedFileError,
    detect_mime,
    prepare,
)

DATASET = Path(__file__).resolve().parents[2] / "eval" / "dataset"
GROUND_TRUTHS = [
    json.loads(p.read_text(encoding="utf-8"))
    for p in sorted((DATASET / "ground_truth").glob("inv-*.json"))
]


def _blank_pdf(pages: int, *, password: str | None = None) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    if password:
        writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _text_pdf(text: str) -> bytes:
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(300, 300))
    pdf.drawString(20, 150, text)
    pdf.save()
    return buffer.getvalue()


# --- sniffing and limits ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (b"%PDF-1.7 ...", "application/pdf"),
        (b"\xff\xd8\xff\xe1 exif", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n rest", "image/png"),
        (b"GIF89a", None),
        (b"PK\x03\x04", None),
        (b"", None),
    ],
)
def test_detect_mime(data: bytes, mime: str | None) -> None:
    assert detect_mime(data) == mime


def test_unsupported_type_is_rejected_with_plain_message() -> None:
    with pytest.raises(UnsupportedFileError, match="unsupported file type"):
        prepare(b"GIF89a\x00\x00")


def test_size_limit_is_checked_before_parsing() -> None:
    data = b"%PDF-1.4" + b"\x00" * 100
    with pytest.raises(FileTooLargeError, match="limit is 0 MB"):
        prepare(data, limits=InputLimits(max_bytes=50))
    prepared = prepare(b"\xff\xd8\xff" + b"\x00" * 10, limits=InputLimits(max_bytes=13))
    assert prepared.input_kind == "photo"


def test_page_limit() -> None:
    six = _blank_pdf(6)
    with pytest.raises(TooManyPagesError, match="6 pages, the limit is 5"):
        prepare(six)
    assert prepare(six, limits=InputLimits(max_pages=6)).pages == 6
    assert prepare(_blank_pdf(5)).pages == 5


def test_encrypted_and_broken_pdfs_are_reported() -> None:
    with pytest.raises(UnreadablePdfError, match="password"):
        prepare(_blank_pdf(1, password="secret"))
    with pytest.raises(UnreadablePdfError):
        prepare(b"%PDF-1.4\ngarbage without xref")


def test_images_are_passed_through_untouched() -> None:
    data = b"\x89PNG\r\n\x1a\n" + b"\x01" * 100
    prepared = prepare(data)
    assert prepared.input_kind == "photo" and prepared.mime == "image/png"
    assert prepared.input.data is data  # same object, no copy
    assert prepared.pages == 1 and prepared.size_bytes == len(data)


def test_pdf_with_too_little_text_counts_as_scan() -> None:
    data = _text_pdf("Page 1")
    prepared = prepare(data)
    assert prepared.input_kind == "scan_pdf"
    assert prepared.input.data is data and prepared.input.mime == "application/pdf"
    rich = _text_pdf("INVOICE 2026-0001 " + "total 1234.56 EUR " * 20)
    assert prepare(rich).input_kind == "text_pdf"
    assert TEXT_LAYER_MIN_CHARS == 200


# --- whole eval dataset ----------------------------------------------------------------


@pytest.mark.parametrize("gt", GROUND_TRUTHS, ids=[gt["id"] for gt in GROUND_TRUTHS])
def test_input_kind_matches_dataset(gt: dict[str, Any]) -> None:
    data = (DATASET / gt["file"]).read_bytes()
    prepared = prepare(data)
    assert prepared.input_kind == gt["input_type"]
    assert prepared.pages == gt["pages"]
    if gt["input_type"] == "text_pdf":
        text = prepared.input.text
        assert text is not None
        printed = gt["printed"]
        if printed["invoice_number"]:
            assert printed["invoice_number"] in text
        assert printed["total"] in text
    else:
        assert prepared.input.data is data
