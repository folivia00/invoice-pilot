"""Fixed bot texts (Telegram HTML)."""

from decimal import Decimal
from typing import Final

from invoice_pilot.services.stats import PeriodStats, StatsSnapshot

HINT: Final = "Send me an invoice as a PDF, a scan or a photo."


def start_text(*, retention_days: int, remaining_today: int, daily_limit: int) -> str:
    """Greeting with usage, the privacy disclaimer and today's remaining documents."""
    return (
        "<b>Invoice Pilot</b> extracts invoice fields with AI and checks them with "
        "deterministic rules (sums, dates, IBAN, tax IDs, currency).\n\n"
        "Send a PDF, scan or photo of an invoice. You get the fields and a status: "
        "approved automatically, or needs review with the reasons.\n\n"
        "<i>Demo. Do not upload real or confidential documents. Files are processed in memory "
        f"and never stored; extracted fields are deleted after {retention_days} days. "
        "This demo uses the Gemini API free tier, where Google may use inputs to improve "
        "its products.</i>\n\n"
        f"Today you can process {remaining_today} more of {daily_limit} documents (UTC day)."
    )


NOTHING_TO_EXPORT: Final = "Nothing to export yet."
EXPORT_PROMPT: Final = "Export your documents of the last {days} days:"
DOCUMENT_UNAVAILABLE: Final = "This document is no longer available."


def _documents_line(documents: dict[str, int]) -> str:
    if not documents:
        return "Documents: 0"
    parts = ", ".join(f"{status} {count}" for status, count in documents.items())
    return f"Documents: {sum(documents.values())} ({parts})"


def _period_lines(title: str, period: PeriodStats) -> list[str]:
    return [
        f"<b>{title}</b>",
        _documents_line(period.documents),
        f"Users: {period.users}",
        f"LLM calls: ok {period.calls_ok} · 429 {period.calls_429} · "
        f"other errors {period.calls_other_errors}",
        f"Tokens: in {period.input_tokens} · out {period.output_tokens}",
        f"Cost: ${period.cost_usd.quantize(Decimal('0.0001'))} (paid-tier prices)",
    ]


def stats_text(snapshot: StatsSnapshot) -> str:
    """Admin statistics (counts and money only; no content, no user IDs)."""
    lines = ["<b>Stats</b> (UTC)", ""]
    lines += _period_lines("Today", snapshot.today)
    lines.append("")
    lines += _period_lines(f"Last {snapshot.retention_days} days", snapshot.retention)
    lines += [
        "",
        f"<b>Left today:</b> {snapshot.docs_left_today} documents · "
        f"${snapshot.budget_left_today_usd.quantize(Decimal('0.0001'))}",
        f"<b>Queue:</b> {snapshot.queue_waiting} waiting · {snapshot.queue_active} processing",
    ]
    return "\n".join(lines)
