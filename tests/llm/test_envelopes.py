"""Malformed response envelopes become ProviderError; abnormal Gemini finish reasons are invalid."""

from typing import Any

import httpx
import pytest

from invoice_pilot.llm.errors import InvalidResponseError, ProviderError
from invoice_pilot.llm.providers.gemini import FINISH_REASON_OK, GeminiProvider
from invoice_pilot.llm.providers.openai import OpenAIProvider
from invoice_pilot.llm.types import DocumentInput

DOC = DocumentInput(text="invoice")


def _gemini(mock_client: Any, fake_api_key: str, payload: Any) -> GeminiProvider:
    handler = lambda r: httpx.Response(200, json=payload)  # noqa: E731
    return GeminiProvider(
        api_key=fake_api_key, model="gemini-3.5-flash-lite", client=mock_client(handler)
    )


def _openai(mock_client: Any, fake_api_key: str, payload: Any) -> OpenAIProvider:
    handler = lambda r: httpx.Response(200, json=payload)  # noqa: E731
    return OpenAIProvider(api_key=fake_api_key, model="gpt-6-luna", client=mock_client(handler))


GEMINI_BROKEN: list[tuple[Any, str]] = [
    ([{"candidates": []}], "not an object"),
    ({"candidates": {"content": {}}}, "candidates is not a list"),
    ({"candidates": ["text"]}, "candidates is not a list of objects"),
    (
        {"candidates": [{"content": "text", "finishReason": "STOP"}]},
        "candidate content is not an object",
    ),
    (
        {"candidates": [{"content": {"parts": "text"}, "finishReason": "STOP"}]},
        "content parts is not a list",
    ),
    (
        {"candidates": [{"content": {"parts": [1, 2]}, "finishReason": "STOP"}]},
        "content parts is not a list",
    ),
    ({"usageMetadata": [1], "candidates": []}, "usageMetadata is not an object"),
    ({"promptFeedback": "blocked", "candidates": []}, "promptFeedback is not an object"),
]


@pytest.mark.parametrize(("payload", "fragment"), GEMINI_BROKEN)
def test_gemini_malformed_envelopes(
    mock_client: Any, fake_api_key: str, run_async: Any, payload: Any, fragment: str
) -> None:
    with pytest.raises(ProviderError, match=fragment):
        run_async(_gemini(mock_client, fake_api_key, payload).extract(DOC))


OPENAI_BROKEN: list[tuple[Any, str]] = [
    (["completed"], "not an object"),
    ({"status": "completed", "output": {"type": "message"}}, "output is not a list"),
    ({"status": "completed", "output": ["message"]}, "output is not a list of objects"),
    (
        {"status": "completed", "output": [{"type": "message", "content": "text"}]},
        "message content is not a list",
    ),
    (
        {"status": "completed", "output": [{"type": "message", "content": [["x"]]}]},
        "message content is not a list",
    ),
    ({"status": "completed", "usage": 12, "output": []}, "usage is not an object"),
    (
        {"status": "completed", "usage": {"output_tokens_details": 3}, "output": []},
        "output_tokens_details",
    ),
    ({"status": "failed", "error": "boom"}, "error is not an object"),
    ({"status": "incomplete", "incomplete_details": "max"}, "incomplete_details is not an object"),
]


@pytest.mark.parametrize(("payload", "fragment"), OPENAI_BROKEN)
def test_openai_malformed_envelopes(
    mock_client: Any, fake_api_key: str, run_async: Any, payload: Any, fragment: str
) -> None:
    with pytest.raises(ProviderError, match=fragment):
        run_async(_openai(mock_client, fake_api_key, payload).extract(DOC))


@pytest.mark.parametrize(
    "reason",
    ["MAX_TOKENS", "SAFETY", "RECITATION", "PROHIBITED_CONTENT", "MALFORMED_RESPONSE", "OTHER"],
)
def test_gemini_abnormal_finish_reason_with_text_is_invalid(
    mock_client: Any,
    fake_api_key: str,
    gemini_envelope: Any,
    invoice_json: Any,
    run_async: Any,
    reason: str,
) -> None:
    envelope = gemini_envelope(invoice_json(), finish_reason=reason)
    with pytest.raises(InvalidResponseError, match=f"finishReason={reason}") as info:
        run_async(_gemini(mock_client, fake_api_key, envelope).extract(DOC))
    (record,) = info.value.attempts
    assert record.status == "invalid_response" and record.output_tokens == 70


def test_gemini_stop_and_missing_finish_reason_are_accepted(
    mock_client: Any, fake_api_key: str, gemini_envelope: Any, invoice_json: Any, run_async: Any
) -> None:
    assert FINISH_REASON_OK == "STOP"
    envelope = gemini_envelope(invoice_json(), finish_reason="STOP")
    assert (
        run_async(_gemini(mock_client, fake_api_key, envelope).extract(DOC)).raw_text
        == invoice_json()
    )
    del envelope["candidates"][0]["finishReason"]
    assert (
        run_async(_gemini(mock_client, fake_api_key, envelope).extract(DOC)).raw_text
        == invoice_json()
    )
