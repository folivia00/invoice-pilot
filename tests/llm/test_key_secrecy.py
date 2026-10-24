"""The API key never shows up in reprs, exception messages or logs."""

import logging
from datetime import date
from typing import Any

import httpx
import pytest

from invoice_pilot.llm.errors import LLMError, ProviderError, ProviderUnavailableError
from invoice_pilot.llm.providers.gemini import GeminiProvider
from invoice_pilot.llm.providers.openai import OpenAIProvider
from invoice_pilot.llm.recording import RecordingProvider
from invoice_pilot.llm.types import DocumentInput
from invoice_pilot.services.extraction import extract_invoice

PHOTO = b"\xff\xd8\xff\xe0" + b"\x00" * 64  # JPEG signature: goes to the provider as-is


def _handler_400(request: httpx.Request) -> httpx.Response:
    return httpx.Response(400, json={"error": {"message": "bad request", "type": "invalid"}})


def _handler_503(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, text="overloaded")


def test_repr_hides_key(mock_client: Any, fake_api_key: str, tmp_path: Any) -> None:
    gemini = GeminiProvider(
        api_key=fake_api_key, model="gemini-3.5-flash-lite", client=mock_client(_handler_400)
    )
    openai = OpenAIProvider(
        api_key=fake_api_key, model="gpt-6-luna", client=mock_client(_handler_400)
    )
    wrapped = RecordingProvider(gemini, mode="off", root=tmp_path)
    for text in (repr(gemini), str(gemini), repr(openai), str(openai), repr(wrapped)):
        assert fake_api_key not in text


@pytest.mark.parametrize("handler", [_handler_400, _handler_503])
def test_exceptions_hide_key(
    mock_client: Any, fake_api_key: str, fake_sleep: Any, run_async: Any, handler: Any
) -> None:
    for cls, model in ((GeminiProvider, "gemini-3.5-flash-lite"), (OpenAIProvider, "gpt-6-luna")):
        provider = cls(
            api_key=fake_api_key,
            model=model,
            client=mock_client(handler),
            sleep=fake_sleep,
            jitter=lambda: 0.0,
        )
        with pytest.raises((ProviderError, ProviderUnavailableError)) as info:
            run_async(provider.extract(DocumentInput(text="x")))
        assert fake_api_key not in str(info.value)
        assert fake_api_key not in repr(info.value)
        assert fake_api_key not in repr(info.value.__dict__)


def test_logs_hide_key(
    mock_client: Any, fake_api_key: str, fake_sleep: Any, run_async: Any, caplog: Any
) -> None:
    provider = OpenAIProvider(
        api_key=fake_api_key,
        model="gpt-6-luna",
        client=mock_client(_handler_503),
        sleep=fake_sleep,
        jitter=lambda: 0.0,
    )
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(LLMError):
            run_async(extract_invoice(PHOTO, provider=provider, today=date(2026, 10, 24)))
    assert caplog.records, "the service logs the failure"
    assert fake_api_key not in caplog.text
