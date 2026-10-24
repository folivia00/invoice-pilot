"""Gemini provider: request construction and response envelope parsing (MockTransport)."""

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from invoice_pilot.llm.errors import InvalidResponseError, ProviderError
from invoice_pilot.llm.prompts import load_prompt
from invoice_pilot.llm.providers.gemini import GEMINI_BASE_URL, GeminiProvider
from invoice_pilot.llm.schema import INVOICE_SCHEMA
from invoice_pilot.llm.types import DocumentInput

PDF_BYTES = b"%PDF-1.7\n%fake binary body \x00\x01\x02\n%%EOF\n"
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"\xff\xd9"


def _provider(
    mock_client: Callable[..., httpx.AsyncClient],
    fake_api_key: str,
    handler: Callable[[httpx.Request], httpx.Response],
    **kwargs: Any,
) -> GeminiProvider:
    kwargs.setdefault("model", "gemini-3.5-flash-lite")
    return GeminiProvider(api_key=fake_api_key, client=mock_client(handler), **kwargs)


def _capture(envelope: dict[str, Any]) -> tuple[list[httpx.Request], Callable[..., Any]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=envelope)

    return seen, handler


# --- requests ------------------------------------------------------------------------


def test_text_request_shape(
    mock_client: Any, fake_api_key: str, gemini_envelope: Any, invoice_json: Any, run_async: Any
) -> None:
    seen, handler = _capture(gemini_envelope(invoice_json()))
    provider = _provider(mock_client, fake_api_key, handler)
    response = run_async(provider.extract(DocumentInput(text="Invoice no. 1 total 10.00")))

    request = seen[0]
    assert str(request.url) == f"{GEMINI_BASE_URL}/models/gemini-3.5-flash-lite:generateContent"
    assert request.headers["x-goog-api-key"] == fake_api_key
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
    assert body["systemInstruction"]["parts"] == [{"text": load_prompt()}]
    parts = body["contents"][0]["parts"]
    assert body["contents"][0]["role"] == "user"
    assert parts[-1] == {"text": "Invoice no. 1 total 10.00"}
    generation = body["generationConfig"]
    assert generation["responseFormat"] == {
        "text": {"mimeType": "APPLICATION_JSON", "schema": INVOICE_SCHEMA}
    }
    assert generation["thinkingConfig"] == {"thinkingLevel": "MINIMAL"}
    assert "mediaResolution" not in generation and "temperature" not in generation
    assert "responseSchema" not in generation and "responseMimeType" not in generation
    assert response.raw_text == invoice_json()


@pytest.mark.parametrize(
    ("data", "mime"), [(PDF_BYTES, "application/pdf"), (JPEG_BYTES, "image/jpeg")]
)
def test_binary_request_uses_inline_base64(
    mock_client: Any,
    fake_api_key: str,
    gemini_envelope: Any,
    invoice_json: Any,
    run_async: Any,
    data: bytes,
    mime: str,
) -> None:
    seen, handler = _capture(gemini_envelope(invoice_json()))
    provider = _provider(mock_client, fake_api_key, handler, media_resolution="medium")
    run_async(provider.extract(DocumentInput(data=data, mime=mime)))
    body = json.loads(seen[0].content)
    inline = body["contents"][0]["parts"][-1]["inlineData"]
    assert inline["mimeType"] == mime
    assert base64.b64decode(inline["data"]) == data
    assert body["generationConfig"]["mediaResolution"] == "MEDIA_RESOLUTION_MEDIUM"


def test_media_resolution_is_not_sent_for_text(mock_client: Any, fake_api_key: str) -> None:
    provider = _provider(
        mock_client, fake_api_key, lambda r: httpx.Response(500), media_resolution="high"
    )
    body = provider.request_body(DocumentInput(text="x"))
    assert "mediaResolution" not in body["generationConfig"]
    body = provider.request_body(DocumentInput(data=PDF_BYTES, mime="application/pdf"))
    assert body["generationConfig"]["mediaResolution"] == "MEDIA_RESOLUTION_HIGH"


@pytest.mark.parametrize(
    ("model", "level", "expected"),
    [
        ("gemini-3.5-flash-lite", None, "MINIMAL"),
        ("gemini-3.8-flash", None, "LOW"),
        ("gemini-9-future", None, "LOW"),
        ("gemini-3.5-flash-lite", "high", "HIGH"),
    ],
)
def test_thinking_level_defaults_to_lowest_allowed(
    mock_client: Any, fake_api_key: str, model: str, level: str | None, expected: str
) -> None:
    provider = _provider(
        mock_client, fake_api_key, lambda r: httpx.Response(500), model=model, thinking_level=level
    )
    body = provider.request_body(DocumentInput(text="x"))
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": expected}
    assert provider.params["thinking_level"] == expected.lower()


def test_invalid_parameters_are_rejected(mock_client: Any, fake_api_key: str) -> None:
    handler = lambda r: httpx.Response(500)  # noqa: E731
    with pytest.raises(ValueError):
        _provider(mock_client, fake_api_key, handler, thinking_level="ultra")
    with pytest.raises(ValueError):
        _provider(mock_client, fake_api_key, handler, media_resolution="ultra_high")
    with pytest.raises(ValueError):
        GeminiProvider(api_key="", model="m", client=mock_client(handler))


# --- responses -----------------------------------------------------------------------


def test_usage_counts_thoughts_as_output(
    mock_client: Any, fake_api_key: str, gemini_envelope: Any, invoice_json: Any, run_async: Any
) -> None:
    envelope = gemini_envelope(
        invoice_json(), prompt_tokens=1234, candidate_tokens=300, thoughts_tokens=45
    )
    _, handler = _capture(envelope)
    response = run_async(
        _provider(mock_client, fake_api_key, handler).extract(DocumentInput(text="x"))
    )
    usage = response.usage
    assert (usage.input_tokens, usage.output_tokens, usage.thinking_tokens) == (1234, 345, 45)
    assert usage.status == "ok" and usage.http_status == 200
    assert usage.provider == "gemini" and usage.model == "gemini-3.5-flash-lite"
    assert response.attempts == (usage,)


def test_thought_parts_are_skipped_and_text_parts_joined(
    mock_client: Any, fake_api_key: str, gemini_envelope: Any, run_async: Any
) -> None:
    envelope = gemini_envelope("ignored")
    envelope["candidates"][0]["content"]["parts"] = [
        {"text": "secret reasoning", "thought": True},
        {"text": '{"a":'},
        {"text": " 1}"},
    ]
    _, handler = _capture(envelope)
    response = run_async(
        _provider(mock_client, fake_api_key, handler).extract(DocumentInput(text="x"))
    )
    assert response.raw_text == '{"a": 1}'


@pytest.mark.parametrize(
    ("envelope_kwargs", "fragment"),
    [
        ({"text": None, "finish_reason": "MAX_TOKENS"}, "finishReason=MAX_TOKENS"),
        ({"text": "   "}, "empty answer"),
        ({"text": None, "block_reason": "PROHIBITED_CONTENT"}, "blocked"),
    ],
)
def test_unusable_answers_raise_invalid_response(
    mock_client: Any,
    fake_api_key: str,
    gemini_envelope: Any,
    run_async: Any,
    envelope_kwargs: dict[str, Any],
    fragment: str,
) -> None:
    _, handler = _capture(gemini_envelope(**envelope_kwargs))
    with pytest.raises(InvalidResponseError) as info:
        run_async(_provider(mock_client, fake_api_key, handler).extract(DocumentInput(text="x")))
    assert fragment in str(info.value)
    (record,) = info.value.attempts
    assert record.status == "invalid_response" and record.input_tokens == 100


def test_no_candidates_raises_invalid_response(
    mock_client: Any, fake_api_key: str, run_async: Any
) -> None:
    _, handler = _capture({"usageMetadata": {"promptTokenCount": 7}})
    with pytest.raises(InvalidResponseError, match="no candidates"):
        run_async(_provider(mock_client, fake_api_key, handler).extract(DocumentInput(text="x")))


def test_non_json_envelope_is_a_provider_error(
    mock_client: Any, fake_api_key: str, run_async: Any
) -> None:
    handler = lambda r: httpx.Response(200, text="<html>upstream</html>")  # noqa: E731
    with pytest.raises(ProviderError, match="not JSON"):
        run_async(_provider(mock_client, fake_api_key, handler).extract(DocumentInput(text="x")))
