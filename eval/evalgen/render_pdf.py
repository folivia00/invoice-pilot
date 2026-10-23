"""PDF output with reportlab: vector text PDFs and image-only (scan) PDFs.

``invariant=True`` makes reportlab write fixed metadata (creation date,
document ID) so the same operations always produce byte-identical files.
"""

import io

from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from evalgen.layout import Line, Page, Rect, Text, font_name, register_fonts


def _canvas(buffer: io.BytesIO, page: Page) -> canvas.Canvas:
    return canvas.Canvas(
        buffer, pagesize=(page.width, page.height), invariant=True, pageCompression=1
    )


def render_text_pdf(pages: list[Page]) -> bytes:
    """Draw every operation as vector graphics / real text."""
    register_fonts()
    buffer = io.BytesIO()
    pdf = _canvas(buffer, pages[0])
    pdf.setTitle("Invoice")
    for page in pages:
        pdf.setPageSize((page.width, page.height))
        h = page.height
        for op in page.ops:
            if isinstance(op, Rect):
                if op.fill is not None:
                    pdf.setFillColorRGB(*op.fill)
                if op.stroke is not None:
                    pdf.setStrokeColorRGB(*op.stroke)
                    pdf.setLineWidth(op.width)
                pdf.rect(
                    op.x,
                    h - op.y - op.h,
                    op.w,
                    op.h,
                    stroke=op.stroke is not None,
                    fill=op.fill is not None,
                )
            elif isinstance(op, Line):
                pdf.setStrokeColorRGB(*op.color)
                pdf.setLineWidth(op.width)
                pdf.line(op.x1, h - op.y1, op.x2, h - op.y2)
            elif isinstance(op, Text):
                pdf.setFillColorRGB(*op.color)
                pdf.setFont(font_name(op.serif, op.bold), op.size)
                pdf.drawString(op.x, h - op.y, op.text)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def render_image_pdf(jpeg_pages: list[bytes], page_size: tuple[float, float]) -> bytes:
    """One full-page JPEG per page, no text layer (what a scanner produces)."""
    buffer = io.BytesIO()
    width, height = page_size
    pdf = canvas.Canvas(buffer, pagesize=page_size, invariant=True)
    pdf.setTitle("Scanned document")
    for data in jpeg_pages:
        pdf.drawImage(ImageReader(io.BytesIO(data)), 0, 0, width=width, height=height)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()
