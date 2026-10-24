"""OpenAI Responses provider: request construction and envelope parsing (MockTransport)."""

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from invoice_pilot.llm.errors import InvalidResponseError, ProviderError
from invoice_pilot.llm.prompts import load_prompt
from invoice_pilot.llm.providers.openai import OPENAI_RESPONSES_URL, OpenAIProvider
from invoice_pilot.llm.schema import INVOICE_SCHEMA, SCHEMA_NAME
from invoice_pilot.llm.types import DocumentInput

PDF_BYTES = b"%PDF-1.7\n%fake binary body \x00\x01\x02\n%%EOF\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


def _provider(
    mock_client: Callable[..., httpx.AsyncClient],
    fake_api_key: str,
    handler: Callable[[httpx.Request], httpx.Response],
    **kwargs: Any,
) -> OpenAIProvider:
    kwargs.setdefault("model", "gpt-6-luna")
    return OpenAIProvider(api_key=fake_api_key, client=mock_client(handler), **kwargs)


def _capture(envelope: dict[str, Any]) -> tuple[list[httpx.Request], Callable[..., Any]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=envelope)

    return seen, handler


def test_text_request_shape(
    mock_client: Any, fake_api_key: str, openai_envelope: Any, invoice_json: Any, run_async: Any
) -> None:
    seen, handler = _capture(openai_envelope(invoice_json()))
    response = run_async(
        _provider(mock_client, fake_api_key, handler).extract(DocumentInput(text="Invoice text"))
    )
    request = seen[0]
    assert str(request.url) == OPENAI_RESPONSES_URL
    assert request.headers["authorization"] == f"Bearer {fake_api_key}"
    body = json.loads(request.content)
    assert body["model"] == "gpt-6-luna"
    assert body["instructions"] == load_prompt()
    content = body["input"][0]["content"]
    assert body["input"][0]["role"] == "user"
    assert content[-1] == {"type": "input_text", "text": "Invoice text"}
    assert body["text"]["format"] == {
        "type": "json_schema",
        "name": SCHEMA_NAME,
        "schema": INVOICE_SCHEMA,
        "strict": True,
    }
    assert body["reasoning"] == {"effort": "none"}
    assert body["store"] is False
    assert body["max_output_tokens"] == 8192
    assert response.raw_text == invoice_json()


def test_pdf_goes_as_input_file_with_data_url(mock_client: Any, fake_api_key: str) -> None:
    provider = _provider(mock_client, fake_api_key, lambda r: httpx.Response(500))
    part = provider.request_body(DocumentInput(data=PDF_BYTES, mime="application/pdf"))
    part = part["input"][0]["content"][-1]
    assert part["type"] == "input_file" and part["filename"] == "document.pdf"
    prefix = "data:application/pdf;base64,"
    assert part["file_data"].startswith(prefix)
    assert base64.b64decode(part["file_data"][len(prefix) :]) == PDF_BYTES
    assert "detail" not in part


@pytest.mark.parametrize("detail", ["auto", "low", "high"])
def test_image_goes_as_input_image_with_detail(
    mock_client: Any, fake_api_key: str, detail: str
) -> None:
    provider = _provider(
        mock_client, fake_api_key, lambda r: httpx.Response(500), image_detail=detail
    )
    body = provider.request_body(DocumentInput(data=PNG_BYTES, mime="image/png"))
    part = body["input"][0]["content"][-1]
    prefix = "data:image/png;base64,"
    assert part["type"] == "input_image" and part["detail"] == detail
    assert base64.b64decode(part["image_url"][len(prefix) :]) == PNG_BYTES
    assert provider.params == {"image_detail": detail, "reasoning_effort": "none"}


def test_reasoning_is_omitted_when_not_configured(mock_client: Any, fake_api_key: str) -> None:
    provider = _provider(
        mock_client, fake_api_key, lambda r: httpx.Response(500), reasoning_effort=None
    )
    assert "reasoning" not in provider.request_body(DocumentInput(text="x"))
    assert provider.params["reasoning_effort"] == "default"


def test_invalid_parameters_are_rejected(mock_client: Any, fake_api_key: str) -> None:
    handler = lambda r: httpx.Response(500)  # noqa: E731
    with pytest.raises(ValueError):
        _provider(mock_client, fake_api_key, handler, image_detail="ultra")
    with pytest.raises(ValueError):
        _provider(mock_client, fake_api_key, handler, reasoning_effort="turbo")
    with pytest.raises(ValueError):
        OpenAIProvider(api_key="", model="m", client=mock_client(handler))


def test_usage_includes_reasoning_tokens(
    mock_client: Any, fake_api_key: str, openai_envelope: Any, invoice_json: Any, run_async: Any
) -> None:
    envelope = openai_envelope(
        invoice_json(), input_tokens=900, output_tokens=130, reasoning_tokens=60
    )
    _, handler = _capture(envelope)
    response = run_async(
        _provider(mock_client, fake_api_key, handler).extract(DocumentInput(text="x"))
    )
    usage = response.usage
    assert (usage.input_tokens, usage.output_tokens, usage.thinking_tokens) == (900, 130, 60)
    assert usage.provider == "openai" and usage.status == "ok"


def test_refusal_is_an_invalid_response(
    mock_client: Any, fake_api_key: str, openai_envelope: Any, run_async: Any
) -> None:
    _, handler = _capture(openai_envelope(None, refusal="I cannot help with that."))
    with pytest.raises(InvalidResponseError, match="refused") as info:
        run_async(_provider(mock_client, fake_api_key, handler).extract(DocumentInput(text="x")))
    (record,) = info.value.attempts
    assert record.status == "invalid_response" and record.output_tokens == 70
    assert "I cannot help" not in str(info.value)


def test_incomplete_response_is_invalid(
    mock_client: Any, fake_api_key: str, openai_envelope: Any, run_async: Any
) -> None:
    envelope = openai_envelope(None, status="incomplete", incomplete_reason="max_output_tokens")
    _, handler = _capture(envelope)
    with pytest.raises(InvalidResponseError, match="incomplete .max_output_tokens"):
        run_async(_provider(mock_client, fake_api_key, handler).extract(DocumentInput(text="x")))


def test_empty_message_is_invalid(
    mock_client: Any, fake_api_key: str, openai_envelope: Any, run_async: Any
) -> None:
    _, handler = _capture(openai_envelope(None))
    with pytest.raises(InvalidResponseError, match="empty"):
        run_async(_provider(mock_client, fake_api_key, handler).extract(DocumentInput(text="x")))


def test_error_object_in_200_body_is_a_provider_error(
    mock_client: Any, fake_api_key: str, openai_envelope: Any, run_async: Any
) -> None:
    envelope = openai_envelope(None, status="failed")
    envelope["error"] = {"code": "server_error", "message": "boom"}
    _, handler = _capture(envelope)
    with pytest.raises(ProviderError, match="server_error"):
        run_async(_provider(mock_client, fake_api_key, handler).extract(DocumentInput(text="x")))
