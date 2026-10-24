"""extract_invoice(prepared=...) skips the second prepare() pass."""

from datetime import date
from typing import Any

import invoice_pilot.services.extraction as service_module
from invoice_pilot.domain import ValidationStatus
from invoice_pilot.extraction.prepare import prepare
from invoice_pilot.llm.types import DocumentInput, ProviderResponse, UsageRecord
from invoice_pilot.services.extraction import extract_invoice

PHOTO = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class FakeProvider:
    name = "fake"
    model = "fake-1"
    params: dict[str, str] = {}

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.docs: list[DocumentInput] = []

    async def extract(self, doc: DocumentInput) -> ProviderResponse:
        self.docs.append(doc)
        usage = UsageRecord(self.name, self.model, "ok", 10, 5, 0, 1, 200)
        return ProviderResponse(raw_text=self.answer, usage=usage, attempts=(usage,))


def test_prepared_input_is_used_without_a_second_prepare(
    monkeypatch: Any, invoice_json: Any, run_async: Any
) -> None:
    calls = {"n": 0}
    real_prepare = prepare

    def counting(data: bytes, **kwargs: Any) -> Any:
        calls["n"] += 1
        return real_prepare(data, **kwargs)

    monkeypatch.setattr(service_module, "prepare", counting)
    prepared = real_prepare(PHOTO)
    provider = FakeProvider(invoice_json())
    outcome = run_async(
        extract_invoice(PHOTO, provider=provider, today=date(2026, 10, 24), prepared=prepared)
    )
    assert calls["n"] == 0
    assert provider.docs[0] is prepared.input
    assert outcome.input_kind == "photo" and outcome.report.status is ValidationStatus.AUTO_APPROVED


def test_without_prepared_the_service_prepares_once(
    monkeypatch: Any, invoice_json: Any, run_async: Any
) -> None:
    calls = {"n": 0}
    real_prepare = prepare

    def counting(data: bytes, **kwargs: Any) -> Any:
        calls["n"] += 1
        return real_prepare(data, **kwargs)

    monkeypatch.setattr(service_module, "prepare", counting)
    run_async(
        extract_invoice(PHOTO, provider=FakeProvider(invoice_json()), today=date(2026, 10, 24))
    )
    assert calls["n"] == 1
