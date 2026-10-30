"""Result card: HTML escaping, line-item cap, Telegram length limit."""

from datetime import date
from decimal import Decimal

from invoice_pilot.bot.card import (
    MAX_LINE_ITEMS,
    STATUS_APPROVED,
    STATUS_REVIEW,
    TELEGRAM_MESSAGE_LIMIT,
    TRUNCATED_MARK,
    render_card,
    render_result,
    tg_length,
)
from invoice_pilot.domain import (
    Invoice,
    Issue,
    IssueCode,
    LineItem,
    Party,
    Severity,
    ValidationReport,
    ValidationStatus,
)
from invoice_pilot.services.extraction import ExtractionOutcome
from invoice_pilot.services.processing import ProcessingResult, ResultKind


def outcome(invoice: Invoice | None, issues: list[Issue] | None = None) -> ExtractionOutcome:
    issues = issues or []
    status = (
        ValidationStatus.NEEDS_REVIEW
        if any(i.severity is Severity.ERROR for i in issues)
        else ValidationStatus.AUTO_APPROVED
    )
    return ExtractionOutcome(
        invoice=invoice,
        report=ValidationReport(status=status, issues=issues),
        input_kind="text_pdf",
        pages=1,
        attempts=1,
    )


def item(n: int, description: str = "Item") -> LineItem:
    return LineItem(
        description=f"{description} {n}",
        quantity=Decimal(1),
        unit_price=Decimal("10.00"),
        amount=Decimal("10.00"),
    )


def test_values_are_html_escaped() -> None:
    invoice = Invoice(
        supplier=Party(name="<b>Evil</b> & Sons", tax_id="DE<1>"),
        buyer=Party(name="A&B <script>"),
        invoice_number="<i>7</i>",
        line_items=[item(1, "<u>x</u> & y")],
        currency="EUR",
        total=Decimal("10.00"),
    )
    card = render_card(outcome(invoice), model="m<1>", processing_s=1.234)
    assert "&lt;b&gt;Evil&lt;/b&gt; &amp; Sons" in card
    assert "A&amp;B &lt;script&gt;" in card
    assert "&lt;i&gt;7&lt;/i&gt;" in card
    assert "&lt;u&gt;x&lt;/u&gt; &amp; y" in card
    assert "<b>Evil</b>" not in card and "<script>" not in card
    assert card.endswith("<i>m&lt;1&gt; · 1.2 s</i>")


def test_issue_messages_are_escaped_and_split_into_reasons_and_notes() -> None:
    issues = [
        Issue(IssueCode.TOTAL_MISMATCH, "total", Severity.ERROR, "Total <wrong> & more."),
        Issue(IssueCode.TAX_ID_UNVERIFIED, "supplier.tax_id", Severity.WARNING, "Not verified."),
    ]
    card = render_card(outcome(Invoice(), issues), model="m", processing_s=0.5)
    lines = card.split("\n")
    assert lines[0] == STATUS_REVIEW
    assert lines[1:5] == [
        "<b>Reasons:</b>",
        "• Total &lt;wrong&gt; &amp; more.",
        "<b>Notes:</b>",
        "• Not verified.",
    ]


def test_approved_card_layout() -> None:
    invoice = Invoice(
        supplier=Party(name="Acme Ltd", tax_id="GB123456789"),
        buyer=Party(name="Bob"),
        invoice_number="INV-1",
        invoice_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        currency="GBP",
        line_items=[item(1)],
        subtotal=Decimal("10.00"),
        tax_rate=Decimal("20"),
        tax_amount=Decimal("2.00"),
        total=Decimal("12.00"),
        iban="GB82WEST12345698765432",
        bic="WESTGB2L",
    )
    card = render_card(outcome(invoice), model="gemini-3.5-flash-lite", processing_s=2.0)
    assert card.split("\n")[0] == STATUS_APPROVED
    for expected in (
        "<b>Supplier:</b> Acme Ltd (tax ID GB123456789)",
        "<b>Buyer:</b> Bob",
        "<b>Invoice no.:</b> INV-1",
        "<b>Date:</b> 2026-09-01 · <b>Due:</b> 2026-09-15",
        "1. Item 1 — 1 × 10.00 = 10.00",
        "<b>Tax:</b> 2.00 GBP (20 %)",
        "<b>Total:</b> 12.00 GBP",
        "<b>IBAN:</b> GB82WEST12345698765432 · <b>BIC:</b> WESTGB2L",
    ):
        assert expected in card


def test_line_items_are_capped() -> None:
    invoice = Invoice(line_items=[item(n) for n in range(1, 14)])
    card = render_card(outcome(invoice), model="m", processing_s=1.0)
    assert f"{MAX_LINE_ITEMS}. Item {MAX_LINE_ITEMS}" in card
    assert "11. Item 11" not in card
    assert "…and 3 more" in card


def test_long_card_is_truncated_to_the_telegram_limit() -> None:
    long = "é" * 1000  # cut to 300 characters per value
    issues = [
        Issue(IssueCode.MISSING_FIELD, f"f{n}", Severity.ERROR, f"Problem {n}: {long}")
        for n in range(40)
    ]
    invoice = Invoice(line_items=[item(n, long) for n in range(10)])
    card = render_card(outcome(invoice, issues), model="m", processing_s=1.0)
    assert tg_length(card) <= TELEGRAM_MESSAGE_LIMIT
    assert TRUNCATED_MARK in card
    assert card.endswith("<i>m · 1.0 s</i>")
    assert card.startswith(STATUS_REVIEW)


def test_astral_characters_count_twice() -> None:
    issues = [Issue(IssueCode.MISSING_FIELD, "f", Severity.ERROR, "😀" * 299) for _ in range(20)]
    card = render_card(outcome(None, issues), model="m", processing_s=1.0)
    assert tg_length(card) <= TELEGRAM_MESSAGE_LIMIT
    assert len(card) < tg_length(card)


def test_extraction_failed_card_has_no_fields() -> None:
    issue = Issue(
        IssueCode.EXTRACTION_FAILED, "document", Severity.ERROR, "the model returned nothing"
    )
    card = render_card(outcome(None, [issue]), model="m", processing_s=3.0)
    assert card.startswith(STATUS_REVIEW)
    assert "<b>Supplier:</b>" not in card


def test_render_result_escapes_service_messages() -> None:
    result = ProcessingResult(ResultKind.REJECTED, message="Cannot process <this> & that.")
    assert render_result(result) == "Cannot process &lt;this&gt; &amp; that."
