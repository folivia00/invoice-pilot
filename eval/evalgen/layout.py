"""Layout: turns DocContent into drawing operations, one list per page.

This is the single source of truth for what appears where. The text PDF
renderer (reportlab) and the raster renderer (Pillow, used for scans and
photos) both consume these operations, so all three input types show exactly
the same content at the same positions. Coordinates are PDF points with the
origin at the top-left corner and y growing downwards.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from evalgen.content import DocContent

FONTS_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "fonts"
#: (serif, bold) -> registered font name; files are the bundled Liberation fonts.
FONT_NAMES: Final[dict[tuple[bool, bool], str]] = {
    (False, False): "LiberationSans",
    (False, True): "LiberationSans-Bold",
    (True, False): "LiberationSerif",
    (True, True): "LiberationSerif-Bold",
}
FONT_FILES: Final[dict[str, Path]] = {
    "LiberationSans": FONTS_DIR / "LiberationSans-Regular.ttf",
    "LiberationSans-Bold": FONTS_DIR / "LiberationSans-Bold.ttf",
    "LiberationSerif": FONTS_DIR / "LiberationSerif-Regular.ttf",
    "LiberationSerif-Bold": FONTS_DIR / "LiberationSerif-Bold.ttf",
}

Color = tuple[float, float, float]
BLACK: Final[Color] = (0.0, 0.0, 0.0)
WHITE: Final[Color] = (1.0, 1.0, 1.0)
DARK: Final[Color] = (0.16, 0.2, 0.3)
GRAY: Final[Color] = (0.45, 0.45, 0.45)
LIGHT: Final[Color] = (0.9, 0.9, 0.9)
LIGHTER: Final[Color] = (0.95, 0.95, 0.95)

MARGIN: Final[float] = 50.0
ROW_H: Final[float] = 16.0
FIRST_PAGE_ROWS_SHORT: Final[int] = 4  # used for the planned two-page documents
FIRST_PAGE_ROWS: Final[int] = 30


def font_name(serif: bool, bold: bool) -> str:
    return FONT_NAMES[(serif, bold)]


def register_fonts() -> None:
    """Register the bundled Liberation fonts with reportlab (idempotent)."""
    for name, path in FONT_FILES.items():
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(path)))


def text_width(text: str, size: float, bold: bool = False, serif: bool = False) -> float:
    """Width of ``text`` in points, measured with the embedded font."""
    register_fonts()
    return pdfmetrics.stringWidth(text, font_name(serif, bold), size)


@dataclass(frozen=True, slots=True)
class Text:
    x: float
    y: float  # baseline
    text: str
    size: float
    bold: bool = False
    color: Color = BLACK
    serif: bool = False


@dataclass(frozen=True, slots=True)
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    width: float = 0.6
    color: Color = BLACK


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    w: float
    h: float
    fill: Color | None = None
    stroke: Color | None = None
    width: float = 0.6


Op = Text | Line | Rect


@dataclass(slots=True)
class Page:
    width: float
    height: float
    ops: list[Op] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Style:
    """Knobs that make the ten templates look different."""

    title: str  # plain | plain_right | banner_dark | banner_light
    logo: str  # none | left | right
    parties: str  # stacked_left | two_column | supplier_right | supplier_footer
    meta: str  # right | left | under_title
    table: str  # grid | rules | plain | zebra
    totals: str  # right | table | right_boxed
    bank: str  # footer | meta_block | footer_boxed
    units: bool = False  # extra "Unit" column in the line table
    serif: bool = False  # Liberation Serif instead of Liberation Sans


STYLES: Final[dict[str, Style]] = {
    "t01": Style("plain", "none", "stacked_left", "right", "grid", "right", "footer", units=True),
    "t02": Style("plain", "left", "stacked_left", "right", "rules", "right", "footer"),
    "t03": Style(
        "plain_right", "none", "supplier_right", "left", "plain", "table", "meta_block", serif=True
    ),
    "t04": Style(
        "banner_dark", "none", "two_column", "under_title", "grid", "right", "footer", units=True
    ),
    "t05": Style(
        "plain", "none", "two_column", "under_title", "rules", "right", "footer_boxed", serif=True
    ),
    "t06": Style(
        "plain", "none", "supplier_footer", "right", "grid", "table", "footer", units=True
    ),
    "t07": Style("plain", "right", "stacked_left", "left", "zebra", "right_boxed", "footer"),
    "t08": Style(
        "plain_right", "none", "stacked_left", "right", "rules", "table", "meta_block", serif=True
    ),
    "t09": Style(
        "banner_light", "none", "two_column", "under_title", "zebra", "right", "footer", units=True
    ),
    "t10": Style(
        "plain", "left", "supplier_right", "left", "rules", "table", "footer_boxed", serif=True
    ),
}

SERIF_TEMPLATES: Final[tuple[str, ...]] = tuple(k for k, s in STYLES.items() if s.serif)
UNIT_COLUMN_TEMPLATES: Final[tuple[str, ...]] = tuple(k for k, s in STYLES.items() if s.units)


def build_pages(content: DocContent) -> list[Page]:
    """Lay out the document and return its pages."""
    return _Builder(content).build()


class _Builder:
    def __init__(self, content: DocContent) -> None:
        self.c = content
        self.style = STYLES[content.plan.template]
        self.serif = self.style.serif
        self.W, self.H = content.page_size
        self.R = self.W - MARGIN
        self.labels = content.labels
        self.pages: list[Page] = []
        self.page: Page = Page(self.W, self.H)

    # --- primitives ---------------------------------------------------------------

    def width(self, s: str, size: float, bold: bool = False) -> float:
        return text_width(s, size, bold, self.serif)

    def text(
        self,
        x: float,
        y: float,
        s: str,
        size: float = 9.5,
        bold: bool = False,
        color: Color = BLACK,
    ) -> None:
        self.page.ops.append(Text(x, y, s, size, bold, color, self.serif))

    def text_right(
        self,
        right: float,
        y: float,
        s: str,
        size: float = 9.5,
        bold: bool = False,
        color: Color = BLACK,
    ) -> None:
        self.text(right - self.width(s, size, bold), y, s, size, bold, color)

    def text_center(
        self, cx: float, y: float, s: str, size: float, bold: bool, color: Color
    ) -> None:
        self.text(cx - self.width(s, size, bold) / 2, y, s, size, bold, color)

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        width: float = 0.6,
        color: Color = BLACK,
    ) -> None:
        self.page.ops.append(Line(x1, y1, x2, y2, width, color))

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: Color | None = None,
        stroke: Color | None = None,
    ) -> None:
        self.page.ops.append(Rect(x, y, w, h, fill, stroke))

    def fit(self, s: str, max_width: float, size: float, bold: bool = False) -> str:
        while s and self.width(s, size, bold) > max_width:
            s = s[:-1]
        return s

    # --- build -----------------------------------------------------------------------

    def build(self) -> list[Page]:
        c = self.c
        y = self.title_row()
        y = self.header_blocks(y)
        rows_first = FIRST_PAGE_ROWS_SHORT if c.plan.two_pages else FIRST_PAGE_ROWS
        items = c.items
        first, rest = items[:rows_first], items[rows_first:]
        y = self.table(y, first, totals=not rest, continued=bool(rest))
        if rest:
            self.footer()
            self.pages.append(self.page)
            self.page = Page(self.W, self.H)
            y = self.continuation_header()
            y = self.table(y, rest, totals=True, continued=False, first_index=len(first))
        self.payment_block(y)
        self.footer()
        self.pages.append(self.page)
        self.page_numbers()
        return self.pages

    def title_row(self) -> float:
        c, st = self.c, self.style
        title = (
            self.labels["invoice"].upper()
            if st.title.startswith("banner")
            else self.labels["invoice"]
        )
        logo_w, logo_h = 54.0, 28.0
        initials = "".join(w[0] for w in (c.invoice.supplier.name or "X Y").split()[:2]).upper()
        if st.title == "banner_dark":
            self.rect(0, 40, self.W, 40, fill=DARK)
            self.text(MARGIN, 66, title, 18, True, WHITE)
            self.text_right(self.R, 66, c.invoice.supplier.name or "", 11, False, WHITE)
            return 100
        if st.title == "banner_light":
            self.rect(MARGIN, 40, self.W - 2 * MARGIN, 34, fill=LIGHT)
            self.text(MARGIN + 10, 63, title, 18, True)
            self.text_right(self.R - 10, 63, c.invoice.supplier.name or "", 10)
            return 95
        x = MARGIN
        if st.logo == "left":
            self.rect(MARGIN, 44, logo_w, logo_h, fill=DARK)
            self.text_center(MARGIN + logo_w / 2, 63, initials, 12, True, WHITE)
            x = MARGIN + logo_w + 12
        if st.logo == "right":
            self.rect(self.R - logo_w, 44, logo_w, logo_h, fill=DARK)
            self.text_center(self.R - logo_w / 2, 63, initials, 12, True, WHITE)
        if st.title == "plain_right":
            self.text_right(
                self.R - (logo_w + 12 if st.logo == "right" else 0), 68, title, 20, True
            )
            self.text(x, 68, c.invoice.supplier.name or "", 11, True)
        else:
            self.text(x, 68, title, 20, True)
            if st.parties == "supplier_footer":
                self.text_right(self.R, 68, c.invoice.supplier.name or "", 11, True)
        return 95

    # --- header blocks -----------------------------------------------------------------

    def party_block(
        self,
        x: float,
        y: float,
        label: str,
        lines: list[str],
        tax_label: str | None,
        tax_id: str | None,
    ) -> float:
        self.text(x, y, label.upper(), 7.5, False, GRAY)
        y += 13
        self.text(x, y, self.fit(lines[0], 230, 10, True), 10, True)
        y += 13
        for ln in lines[1:]:
            self.text(x, y, self.fit(ln, 230, 9.5), 9.5)
            y += 12
        if tax_id and tax_label:
            self.text(x, y, f"{tax_label}: {tax_id}", 9)
            y += 12
        return y

    def meta_rows(self) -> list[tuple[str, str, bool]]:
        c = self.c
        rows: list[tuple[str, str, bool]] = []
        if c.printed["invoice_number"]:
            rows.append((self.labels["invoice_number"], c.printed["invoice_number"], True))
        rows.append((self.labels["invoice_date"], c.printed["invoice_date"], False))
        if c.printed["due_date"]:
            rows.append((self.labels["due_date"], c.printed["due_date"], False))
        rows.append((self.labels["customer_number"], c.customer_number, False))
        if self.style.bank == "meta_block" and c.printed["iban"]:
            rows.append((self.labels["iban"], c.printed["iban"], False))
            if c.printed["bic"]:
                rows.append((self.labels["bic"], c.printed["bic"], False))
        return rows

    def meta_block(self, x: float, y: float, width: float) -> float:
        for label, value, bold in self.meta_rows():
            self.text(x, y, label, 9, False, GRAY)
            self.text_right(x + width, y, value, 9.5, bold)
            y += 13
        return y

    def meta_line(self, y: float) -> float:
        x = MARGIN
        for label, value, bold in self.meta_rows():
            if label in (self.labels["iban"], self.labels["bic"]):
                continue
            s = f"{label}: "
            self.text(x, y, s, 9, False, GRAY)
            x += self.width(s, 9)
            self.text(x, y, value, 9.5, bold)
            x += self.width(value, 9.5, bold) + 18
        return y + 16

    def header_blocks(self, y0: float) -> float:
        c, st = self.c, self.style
        tax_label = c.extra["tax_id_label"]
        sup_tax = c.printed["supplier_tax_id"] or None
        buy_tax = c.printed["buyer_tax_id"] or None
        col2 = MARGIN + (self.W - 2 * MARGIN) / 2 + 10
        meta_w = 215.0
        bottoms: list[float] = []
        y = y0
        if st.meta == "under_title":
            y = self.meta_line(y0) + 6
        sup, buy = self.labels["supplier"], self.labels["buyer"]
        if st.parties == "stacked_left":
            b = self.party_block(MARGIN, y, sup, c.supplier_lines, tax_label, sup_tax)
            b = self.party_block(MARGIN, b + 10, buy, c.buyer_lines, tax_label, buy_tax)
            bottoms.append(b)
        elif st.parties == "two_column":
            bottoms.append(self.party_block(MARGIN, y, sup, c.supplier_lines, tax_label, sup_tax))
            bottoms.append(self.party_block(col2, y, buy, c.buyer_lines, tax_label, buy_tax))
        elif st.parties == "supplier_right":
            bottoms.append(self.party_block(MARGIN, y, buy, c.buyer_lines, tax_label, buy_tax))
            bottoms.append(
                self.party_block(self.R - 230, y, sup, c.supplier_lines, tax_label, sup_tax)
            )
        else:  # supplier_footer: supplier details go to the footer
            bottoms.append(self.party_block(MARGIN, y, buy, c.buyer_lines, tax_label, buy_tax))
        if st.meta == "right":
            bottoms.append(self.meta_block(self.R - meta_w, y, meta_w))
        elif st.meta == "left":
            bottoms.append(self.meta_block(MARGIN, max(bottoms) + 10, meta_w))
        return max(bottoms) + 18

    # --- table ----------------------------------------------------------------------------

    def columns(self) -> tuple[float, float, float, float, float, float]:
        """Column edges: desc start, desc end, qty right, unit right, price right, amount right."""
        x0 = MARGIN
        x4 = self.R
        x3 = self.R - 95  # unit price right edge
        if self.style.units:
            x2u = self.R - 185  # unit right edge
            x2 = self.R - 230  # qty right edge
            x1 = self.R - 290
        else:
            x2u = self.R - 190
            x2 = self.R - 190
            x1 = self.R - 250
        return x0, x1, x2, x2u, x3, x4

    def table(
        self, y: float, rows: list, totals: bool, continued: bool, first_index: int = 0
    ) -> float:
        st = self.style
        x0, x1, x2, x2u, x3, x4 = self.columns()
        head_h = 18.0
        top = y
        if st.table == "zebra":
            self.rect(x0, y, x4 - x0, head_h, fill=LIGHT)
        if st.table == "grid":
            self.rect(x0, y, x4 - x0, head_h, fill=LIGHTER)
        base = y + 12.5
        self.text(x0 + 4, base, self.labels["description"], 9, True)
        self.text_right(x2 - 4, base, self.labels["quantity"], 9, True)
        if st.units:
            self.text(x2 + 4, base, self.labels["unit"], 9, True)
        self.text_right(x3 - 4, base, self.labels["unit_price"], 9, True)
        self.text_right(x4 - 4, base, self.labels["amount"], 9, True)
        y += head_h
        if st.table in ("rules", "plain"):
            self.line(x0, y, x4, y, 0.9)
        for i, item in enumerate(rows):
            if st.table == "zebra" and (first_index + i) % 2 == 1:
                self.rect(x0, y, x4 - x0, ROW_H, fill=LIGHTER)
            base = y + 11.5
            self.text(x0 + 4, base, self.fit(item.description, x1 - x0 - 8, 9.5))
            self.text_right(x2 - 4, base, item.quantity)
            if st.units and item.unit:
                self.text(x2 + 4, base, item.unit)
            self.text_right(x3 - 4, base, item.unit_price)
            self.text_right(x4 - 4, base, item.amount)
            y += ROW_H
            if st.table == "rules":
                self.line(x0, y, x4, y, 0.3, GRAY)
        total_rows_y = y
        if totals and st.totals == "table":
            y = self.totals_in_table(y)
        if st.table == "grid":
            bottom = y
            self.rect(x0, top, x4 - x0, bottom - top, stroke=BLACK)
            self.line(x0, top + head_h, x4, top + head_h, 0.6)
            verticals = (x1, x2, x2u, x3) if st.units else (x1, x2, x3)
            for xx in verticals:
                self.line(xx, top, xx, total_rows_y, 0.5)
            yy = top + head_h
            for _ in rows:
                yy += ROW_H
                self.line(x0, yy, x4, yy, 0.3, GRAY)
        if continued:
            self.text(x0, y + 14, self.labels["continued"], 8.5, False, GRAY)
            return y + 14
        if totals and st.totals != "table":
            y = self.totals_right(y + 10, boxed=st.totals == "right_boxed")
        return y

    def totals_lines(self) -> list[tuple[str, str, bool]]:
        c = self.c
        return [
            (self.labels["subtotal"], c.printed["subtotal"], False),
            (c.tax_label, c.printed["tax_amount"], False),
            (self.labels["total"], c.printed["total"], True),
        ]

    def totals_in_table(self, y: float) -> float:
        x0, x1, x2, x2u, x3, x4 = self.columns()
        for label, value, bold in self.totals_lines():
            base = y + 11.5
            if bold:
                self.line(x3 - 120, y, x4, y, 0.8)
            self.text_right(x3 - 4, base, label, 9.5, bold)
            self.text_right(x4 - 4, base, value, 10 if bold else 9.5, bold)
            y += ROW_H
        return y

    def totals_right(self, y: float, boxed: bool) -> float:
        x0, x1, x2, x2u, x3, x4 = self.columns()
        left = x3 - 120
        start = y
        for label, value, bold in self.totals_lines():
            if bold:
                self.line(left, y, x4, y, 0.8)
                y += 4
            base = y + 11
            self.text(left + 4, base, label, 9.5, bold)
            self.text_right(x4 - 4, base, value, 10.5 if bold else 9.5, bold)
            y += 15
        if boxed:
            self.rect(left - 4, start - 4, x4 - left + 4, y - start + 4, stroke=BLACK)
        return y

    # --- footer and friends ---------------------------------------------------------------

    def payment_block(self, y: float) -> None:
        y += 20
        self.text(MARGIN, y, self.c.payment_terms, 9)
        self.text(MARGIN, y + 13, self.labels["thank_you"], 9)

    def footer(self) -> None:
        c, st = self.c, self.style
        y = self.H - 92
        self.line(MARGIN, y, self.R, y, 0.5, GRAY)
        y += 13
        left: list[str] = []
        right: list[str] = []
        if st.parties == "supplier_footer":
            left.extend(c.supplier_lines)
            if c.printed["supplier_tax_id"]:
                left.append(f"{c.extra['tax_id_label']}: {c.printed['supplier_tax_id']}")
        else:
            left.append(f"{self.labels['phone']}: {c.contact['phone']}")
            left.append(f"{self.labels['email']}: {c.contact['email']}")
            if c.printed["supplier_tax_id"] and st.parties == "two_column":
                left.append(f"{c.extra['tax_id_label']}: {c.printed['supplier_tax_id']}")
        if st.bank != "meta_block" and c.printed["iban"]:
            right.append(self.labels["bank"])
            right.append(f"{self.labels['iban']}: {c.printed['iban']}")
            if c.printed["bic"]:
                right.append(f"{self.labels['bic']}: {c.printed['bic']}")
        if st.bank == "footer_boxed" and right:
            self.rect(
                self.W / 2 - 4, y - 11, self.R - self.W / 2 + 4, 12 * len(right) + 8, stroke=GRAY
            )
        for i, ln in enumerate(left):
            self.text(MARGIN, y + 12 * i, ln, 8.5, False, GRAY if i else BLACK)
        for i, ln in enumerate(right):
            self.text(self.W / 2, y + 12 * i, ln, 8.5, i == 0)

    def continuation_header(self) -> float:
        c = self.c
        self.text(MARGIN, 60, self.labels["invoice"], 14, True)
        number = c.printed["invoice_number"]
        if number:
            self.text_right(self.R, 60, f"{self.labels['invoice_number']} {number}", 9.5)
        self.line(MARGIN, 70, self.R, 70, 0.6)
        return 90

    def page_numbers(self) -> None:
        total = len(self.pages)
        if total < 2:
            return
        for n, page in enumerate(self.pages, start=1):
            label = self.labels["page"].replace("{n}", str(n)).replace("{total}", str(total))
            x = self.R - self.width(label, 8)
            page.ops.append(Text(x, self.H - 30, label, 8, False, GRAY, self.serif))
