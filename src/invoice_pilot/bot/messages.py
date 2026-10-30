"""Fixed bot texts (Telegram HTML)."""

from typing import Final

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
