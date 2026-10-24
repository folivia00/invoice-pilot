"""Retry policy of the shared HTTP layer, exercised through the Gemini provider."""

from typing import Any

import httpx
import pytest

from invoice_pilot.llm.errors import ProviderError, ProviderUnavailableError
from invoice_pilot.llm.http import (
    MAX_ATTEMPTS,
    MAX_DELAY_S,
    RETRY_AFTER_CAP_S,
    parse_retry_after,
    retry_delay,
)
from invoice_pilot.llm.providers.gemini import GeminiProvider
from invoice_pilot.llm.types import DocumentInput

DOC = DocumentInput(text="invoice")


class Script:
    """Answers one scripted response (or raises one scripted exception) per call."""

    def __init__(self, *steps: Any) -> None:
        self.steps = list(steps)
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _provider(mock_client: Any, fake_api_key: str, fake_sleep: Any, handler: Any) -> GeminiProvider:
    return GeminiProvider(
        api_key=fake_api_key,
        model="gemini-3.5-flash-lite",
        client=mock_client(handler),
        sleep=fake_sleep,
        jitter=lambda: 0.0,
    )


def _ok(gemini_envelope: Any, invoice_json: Any) -> httpx.Response:
    return httpx.Response(200, json=gemini_envelope(invoice_json()))


def test_429_with_retry_after_then_success(
    mock_client: Any,
    fake_api_key: str,
    fake_sleep: Any,
    gemini_envelope: Any,
    invoice_json: Any,
    run_async: Any,
) -> None:
    script = Script(
        httpx.Response(
            429, headers={"Retry-After": "7"}, json={"error": {"status": "RESOURCE_EXHAUSTED"}}
        ),
        _ok(gemini_envelope, invoice_json),
    )
    response = run_async(_provider(mock_client, fake_api_key, fake_sleep, script).extract(DOC))
    assert script.calls == 2
    assert fake_sleep.delays == [7.0]
    assert [r.status for r in response.attempts] == ["provider_error", "ok"]
    assert response.attempts[0].http_status == 429 and response.attempts[0].input_tokens == 0


def test_503_twice_then_success(
    mock_client: Any,
    fake_api_key: str,
    fake_sleep: Any,
    gemini_envelope: Any,
    invoice_json: Any,
    run_async: Any,
) -> None:
    script = Script(httpx.Response(503), httpx.Response(503), _ok(gemini_envelope, invoice_json))
    response = run_async(_provider(mock_client, fake_api_key, fake_sleep, script).extract(DOC))
    assert script.calls == 3
    assert fake_sleep.delays == [0.5, 1.0]  # base 1s, 2s with jitter factor 0.5
    assert [r.status for r in response.attempts] == ["provider_error", "provider_error", "ok"]


def test_503_three_times_is_unavailable(
    mock_client: Any, fake_api_key: str, fake_sleep: Any, run_async: Any
) -> None:
    script = Script(*[httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})] * 3)
    with pytest.raises(ProviderUnavailableError, match="HTTP 503 UNAVAILABLE") as info:
        run_async(_provider(mock_client, fake_api_key, fake_sleep, script).extract(DOC))
    assert script.calls == MAX_ATTEMPTS == 3
    assert len(fake_sleep.delays) == 2
    assert [r.status for r in info.value.attempts] == ["provider_error"] * 3
    assert {r.http_status for r in info.value.attempts} == {503}


def test_timeouts_are_retried_then_unavailable(
    mock_client: Any, fake_api_key: str, fake_sleep: Any, run_async: Any
) -> None:
    script = Script(
        httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow"), httpx.ReadTimeout("slow")
    )
    with pytest.raises(ProviderUnavailableError, match="timeout") as info:
        run_async(_provider(mock_client, fake_api_key, fake_sleep, script).extract(DOC))
    assert script.calls == 3
    assert [r.status for r in info.value.attempts] == ["timeout"] * 3
    assert all(r.http_status is None for r in info.value.attempts)


def test_network_error_is_retried(
    mock_client: Any,
    fake_api_key: str,
    fake_sleep: Any,
    gemini_envelope: Any,
    invoice_json: Any,
    run_async: Any,
) -> None:
    script = Script(httpx.ConnectError("refused"), _ok(gemini_envelope, invoice_json))
    response = run_async(_provider(mock_client, fake_api_key, fake_sleep, script).extract(DOC))
    assert script.calls == 2
    assert [r.status for r in response.attempts] == ["provider_error", "ok"]


def test_400_is_not_retried(
    mock_client: Any, fake_api_key: str, fake_sleep: Any, run_async: Any
) -> None:
    script = Script(
        httpx.Response(400, json={"error": {"code": 400, "status": "INVALID_ARGUMENT"}}),
        httpx.Response(200),
    )
    with pytest.raises(ProviderError, match="HTTP 400 INVALID_ARGUMENT") as info:
        run_async(_provider(mock_client, fake_api_key, fake_sleep, script).extract(DOC))
    assert script.calls == 1 and fake_sleep.delays == []
    (record,) = info.value.attempts
    assert record.status == "provider_error" and record.http_status == 400


@pytest.mark.parametrize("status", [401, 403, 404, 413, 422])
def test_other_4xx_are_provider_errors(
    mock_client: Any, fake_api_key: str, fake_sleep: Any, run_async: Any, status: int
) -> None:
    script = Script(httpx.Response(status, text="nope"))
    with pytest.raises(ProviderError, match=f"HTTP {status}"):
        run_async(_provider(mock_client, fake_api_key, fake_sleep, script).extract(DOC))
    assert script.calls == 1


def test_retry_delay_grows_with_jitter_and_caps() -> None:
    assert retry_delay(1, None, 0.0) == 0.5
    assert retry_delay(1, None, 0.5) == 1.0
    assert retry_delay(2, None, 0.0) == 1.0
    assert retry_delay(3, None, 0.0) == 2.0
    assert retry_delay(10, None, 0.99) <= MAX_DELAY_S * 1.5
    assert retry_delay(1, 7.0, 0.0) == 7.0
    assert retry_delay(1, 3600.0, 0.0) == RETRY_AFTER_CAP_S
    assert retry_delay(3, 0.1, 0.0) == 2.0  # a short Retry-After never shortens the backoff


def test_parse_retry_after() -> None:
    assert parse_retry_after(None) is None
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after(" 2.5 ") == 2.5
    assert parse_retry_after("-1") is None
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
