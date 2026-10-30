"""Telegram HTML texts: the result card and the fixed messages.

Every value that comes from the document or the model is escaped. The card is
built line by line (each line closes its own tags), so cutting it to fit the
Telegram limit never breaks the markup.
"""

import html
from decimal import Decimal
from typing import Final

from invoice_pilot.domain import Invoice, Issue, Party, ValidationStatus
from invoice_pilot.services.extraction import ExtractionOutcome
from invoice_pilot.services.processing import ProcessingResult, ResultKind

#: Telegram message limit (UTF-16 code units of the text).
TELEGRAM_MESSAGE_LIMIT: Final = 4096
MAX_LINE_ITEMS: Final = 10
#: A single value is cut to this many characters before escaping.
MAX_VALUE_CHARS: Final = 300
TRUNCATED_MARK: Final = "<i>…message truncated</i>"

STATUS_APPROVED: Final = "✅ <b>Approved automatically</b>"
STATUS_REVIEW: Final = "⚠️ <b>Needs review</b>"


def tg_length(text: str) -> int:
    """Length as Telegram counts it (UTF-16 code units)."""
    return len(text.encode("utf-16-le")) // 2


def esc(value: object) -> str:
    """Escaped, length-capped text of a value ("—" for missing)."""
    if value is None or value == "":
        return "—"
    text = str(value)
    if len(text) > MAX_VALUE_CHARS:
        text = text[: MAX_VALUE_CHARS - 1] + "…"
    return html.escape(text, quote=False)


def _money(value: Decimal | None, currency: str | None) -> str:
    if value is None:
        return "—"
    return f"{esc(value)} {esc(currency)}" if currency else esc(value)


def _party(label: str, party: Party) -> str:
    line = f"<b>{label}:</b> {esc(party.name)}"
    if party.tax_id:
        line += f" (tax ID {esc(party.tax_id)})"
    return line


def _issue_lines(title: str, issues: list[Issue]) -> list[str]:
    if not issues:
        return []
    return [f"<b>{title}:</b>", *(f"• {esc(issue.message)}" for issue in issues)]


def _invoice_lines(invoice: Invoice) -> list[str]:
    lines = [
        _party("Supplier", invoice.supplier),
        _party("Buyer", invoice.buyer),
        f"<b>Invoice no.:</b> {esc(invoice.invoice_number)}",
        f"<b>Date:</b> {esc(invoice.invoice_date)} · <b>Due:</b> {esc(invoice.due_date)}",
        f"<b>Currency:</b> {esc(invoice.currency)}",
    ]
    items = invoice.line_items
    if items:
        lines.append(f"<b>Items ({len(items)}):</b>")
        for index, item in enumerate(items[:MAX_LINE_ITEMS], start=1):
            lines.append(
                f"{index}. {esc(item.description)} — {esc(item.quantity)} × "
                f"{esc(item.unit_price)} = {esc(item.amount)}"
            )
        if len(items) > MAX_LINE_ITEMS:
            lines.append(f"…and {len(items) - MAX_LINE_ITEMS} more")
    tax = _money(invoice.tax_amount, invoice.currency)
    if invoice.tax_rate is not None:
        tax += f" ({esc(invoice.tax_rate)} %)"
    lines += [
        f"<b>Subtotal:</b> {_money(invoice.subtotal, invoice.currency)}",
        f"<b>Tax:</b> {tax}",
        f"<b>Total:</b> {_money(invoice.total, invoice.currency)}",
        f"<b>IBAN:</b> {esc(invoice.iban)} · <b>BIC:</b> {esc(invoice.bic)}",
    ]
    return lines


def render_card(outcome: ExtractionOutcome, *, model: str, processing_s: float) -> str:
    """The result card as Telegram HTML, at most ``TELEGRAM_MESSAGE_LIMIT`` long."""
    report = outcome.report
    approved = report.status is ValidationStatus.AUTO_APPROVED
    lines = [STATUS_APPROVED if approved else STATUS_REVIEW]
    lines += _issue_lines("Reasons", report.errors)
    lines += _issue_lines("Notes", report.warnings)
    if outcome.invoice is not None:
        lines.append("")
        lines += _invoice_lines(outcome.invoice)
    footer = f"<i>{esc(model)} · {processing_s:.1f} s</i>"
    return fit_lines(lines, footer)


def fit_lines(lines: list[str], footer: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> str:
    """Join ``lines`` and ``footer``; drop trailing lines (with a mark) to fit ``limit``."""
    full = "\n".join([*lines, "", footer])
    if tg_length(full) <= limit:
        return full
    tail = ["", TRUNCATED_MARK, footer]
    budget = limit - tg_length("\n".join(tail)) - 1
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = tg_length(line) + 1
        if used + cost > budget:
            break
        kept.append(line)
        used += cost
    return "\n".join([*kept, *tail])


def render_result(result: ProcessingResult) -> str:
    """Card for a processed document, otherwise the escaped service message."""
    if result.kind is ResultKind.DONE and result.outcome is not None:
        return render_card(
            result.outcome, model=result.model or "", processing_s=result.processing_s or 0.0
        )
    return html.escape(result.message or "", quote=False)
