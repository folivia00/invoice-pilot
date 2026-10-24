"""End-to-end pipeline with a fake provider: prepare -> model -> parse -> validate."""

import json
import re
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from invoice_pilot.domain import Invoice, IssueCode, ValidationStatus
from invoice_pilot.extraction.prepare import UnsupportedFileError
from invoice_pilot.llm.errors import InvalidResponseError, ProviderError, ProviderUnavailableError
from invoice_pilot.llm.types import DocumentInput, ProviderResponse, UsageRecord
from invoice_pilot.services.extraction import ExtractionOutcome, extract_invoice

DATASET = Path(__file__).resolve().parents[2] / "eval" / "dataset"
TODAY = date(2026, 10, 1)


def _dumps_numbers(payload: Any) -> str:
    """Ground-truth invoice (Decimal strings) -> model answer with JSON numbers."""
    numeric = {"quantity", "unit_price", "amount", "subtotal", "tax_rate", "tax_amount", "total"}

    def convert(node: Any, key: str | None = None) -> Any:
        if isinstance(node, dict):
            return {k: convert(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [convert(v, key) for v in node]
        if key in numeric and isinstance(node, str):
            return Decimal(node)
        return node

    text = json.dumps(convert(payload), default=lambda d: f"@@RAW:{d}@@")
    return re.sub(r'"@@RAW:([^"]*)@@"', r"\1", text)


class FakeProvider:
    name = "fake"
    model = "fake-1"
    params: dict[str, str] = {}

    def __init__(self, *answers: str | Exception) -> None:
        self.answers = list(answers)
        self.docs: list[DocumentInput] = []

    async def extract(self, doc: DocumentInput) -> ProviderResponse:
        self.docs.append(doc)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        usage = UsageRecord(self.name, self.model, "ok", 500, 120, 0, 80, 200)
        return ProviderResponse(raw_text=answer, usage=usage, attempts=(usage,))


def _clean_ground_truth(input_type: str) -> dict[str, Any]:
    for path in sorted((DATASET / "ground_truth").glob("inv-*.json")):
        gt = json.loads(path.read_text(encoding="utf-8"))
        if gt["input_type"] == input_type and gt["corruption"] is None:
            return gt
    raise AssertionError(input_type)


@pytest.mark.parametrize("input_type", ["text_pdf", "scan_pdf", "photo"])
def test_correct_answer_is_auto_approved(run_async: Any, input_type: str) -> None:
    gt = _clean_ground_truth(input_type)
    data = (DATASET / gt["file"]).read_bytes()
    provider = FakeProvider(_dumps_numbers(gt["invoice"]))
    seen: list[UsageRecord] = []
    outcome = run_async(extract_invoice(data, provider=provider, today=TODAY, on_usage=seen.append))
    assert isinstance(outcome, ExtractionOutcome)
    assert outcome.invoice == Invoice.model_validate(gt["invoice"])
    assert outcome.report.status is ValidationStatus.AUTO_APPROVED
    assert outcome.report.errors == []
    assert outcome.input_kind == input_type and outcome.pages == gt["pages"]
    assert outcome.attempts == 1
    assert [r.status for r in outcome.usage] == ["ok"] and seen == outcome.usage
    assert outcome.input_tokens == 500 and outcome.output_tokens == 120
    doc = provider.docs[0]
    assert doc.is_text == (input_type == "text_pdf")
    if not doc.is_text:
        assert doc.data is data


def test_model_answer_is_passed_through_not_corrected(run_async: Any) -> None:
    gt = _clean_ground_truth("text_pdf")
    wrong = dict(gt["invoice"], total="1.00")
    provider = FakeProvider(_dumps_numbers(wrong))
    outcome = run_async(
        extract_invoice((DATASET / gt["file"]).read_bytes(), provider=provider, today=TODAY)
    )
    assert outcome.invoice is not None and outcome.invoice.total == Decimal("1.00")
    assert outcome.report.status is ValidationStatus.NEEDS_REVIEW
    assert outcome.report.has(IssueCode.TOTAL_MISMATCH)


def test_invalid_answer_is_retried_once(run_async: Any, invoice_json: Any) -> None:
    gt = _clean_ground_truth("photo")
    provider = FakeProvider("not json", invoice_json())
    outcome = run_async(
        extract_invoice((DATASET / gt["file"]).read_bytes(), provider=provider, today=TODAY)
    )
    assert outcome.attempts == 2 and len(provider.docs) == 2
    assert [r.status for r in outcome.usage] == ["invalid_response", "ok"]
    assert outcome.usage[0].input_tokens == 500  # the failed call is billed and recorded
    assert outcome.invoice is not None


def test_two_invalid_answers_give_needs_review(run_async: Any) -> None:
    gt = _clean_ground_truth("scan_pdf")
    provider = FakeProvider("{}", InvalidResponseError("fake: model refused", ()))
    outcome = run_async(
        extract_invoice((DATASET / gt["file"]).read_bytes(), provider=provider, today=TODAY)
    )
    assert outcome.invoice is None and outcome.attempts == 2
    assert outcome.report.status is ValidationStatus.NEEDS_REVIEW
    (issue,) = outcome.report.issues
    assert issue.code is IssueCode.EXTRACTION_FAILED and issue.field == "document"
    assert issue.severity.value == "error" and "model refused" in issue.message
    assert [r.status for r in outcome.usage] == ["invalid_response"]


@pytest.mark.parametrize(
    "error",
    [
        ProviderUnavailableError("fake: unavailable after 3 attempts", ()),
        ProviderError("fake: HTTP 400", ()),
    ],
)
def test_provider_failures_propagate(run_async: Any, error: Exception) -> None:
    gt = _clean_ground_truth("text_pdf")
    failed = UsageRecord("fake", "fake-1", "provider_error", http_status=503)
    error.attempts = (failed, failed, failed)  # type: ignore[attr-defined]
    seen: list[UsageRecord] = []
    with pytest.raises(type(error)):
        run_async(
            extract_invoice(
                (DATASET / gt["file"]).read_bytes(),
                provider=FakeProvider(error),
                today=TODAY,
                on_usage=seen.append,
            )
        )
    assert seen == [failed, failed, failed]


def test_input_errors_happen_before_any_model_call(run_async: Any) -> None:
    provider = FakeProvider()
    with pytest.raises(UnsupportedFileError):
        run_async(extract_invoice(b"GIF89a", provider=provider, today=TODAY))
    assert provider.docs == []


def test_pypdf_is_not_imported_by_the_service_module() -> None:
    code = (
        "import sys, invoice_pilot.services.extraction; "
        "sys.exit(1 if 'pypdf' in sys.modules else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
