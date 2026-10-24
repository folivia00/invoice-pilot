"""Spend limits and the review fixes of the HTTP layer (task 08-fix1)."""

from typing import Any

import httpx
import pytest

from invoice_pilot.llm.errors import ProviderError, ProviderUnavailableError, SpendLimitError
from invoice_pilot.llm.http import RETRY_STATUSES, SPEND_LIMIT_CODES, error_code, is_spend_limit
from invoice_pilot.llm.providers.gemini import GeminiProvider
from invoice_pilot.llm.providers.openai import OpenAIProvider
from invoice_pilot.llm.types import DocumentInput

DOC = DocumentInput(text="invoice")


class Script:
    def __init__(self, *steps: Any) -> None:
        self.steps = list(steps)
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _gemini(mock_client: Any, fake_api_key: str, fake_sleep: Any, handler: Any) -> GeminiProvider:
    return GeminiProvider(
        api_key=fake_api_key,
        model="gemini-3.5-flash-lite",
        client=mock_client(handler),
        sleep=fake_sleep,
        jitter=lambda: 0.0,
    )


def _openai(mock_client: Any, fake_api_key: str, fake_sleep: Any, handler: Any) -> OpenAIProvider:
    return OpenAIProvider(
        api_key=fake_api_key,
        model="gpt-6-luna",
        client=mock_client(handler),
        sleep=fake_sleep,
        jitter=lambda: 0.0,
    )


@pytest.mark.parametrize(
    "code",
    [
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "credit_balance_exhausted",
    ],
)
def test_429_with_a_limit_code_is_not_retried(
    mock_client: Any, fake_api_key: str, fake_sleep: Any, run_async: Any, code: str
) -> None:
    body = {
        "error": {"message": "limit reached, body text", "type": "insufficient_quota", "code": code}
    }
    script = Script(
        httpx.Response(429, headers={"Retry-After": "5"}, json=body), httpx.Response(200)
    )
    provider = _openai(mock_client, fake_api_key, fake_sleep, script)
    with pytest.raises(SpendLimitError) as info:
        run_async(provider.extract(DOC))
    assert script.calls == 1 and fake_sleep.delays == []
    assert info.value.code == code and code in str(info.value)
    assert "body text" not in str(info.value)
    assert isinstance(info.value, ProviderError)
    (record,) = info.value.attempts
    assert record.status == "provider_error" and record.http_status == 429


def test_code_field_wins_over_status_and_type_order(
    mock_client: Any, fake_api_key: str, fake_sleep: Any, run_async: Any
) -> None:
    # error_code() prefers status/type for messages; the limit check reads error.code only
    body = {
        "error": {"status": "RESOURCE_EXHAUSTED", "type": "x", "code": "credit_balance_exhausted"}
    }
    response = httpx.Response(429, json=body)
    assert error_code(response) == "RESOURCE_EXHAUSTED" and is_spend_limit(response)
    script = Script(response)
    with pytest.raises(SpendLimitError, match="credit_balance_exhausted"):
        run_async(_gemini(mock_client, fake_api_key, fake_sleep, script).extract(DOC))
    assert script.calls == 1


def test_402_is_a_spend_limit_without_retry(
    mock_client: Any, fake_api_key: str, fake_sleep: Any, run_async: Any
) -> None:
    body = {"error": {"code": "payment_required", "message": "add credits"}}
    script = Script(httpx.Response(402, json=body), httpx.Response(200))
    with pytest.raises(SpendLimitError, match="HTTP 402 payment_required") as info:
        run_async(_gemini(mock_client, fake_api_key, fake_sleep, script).extract(DOC))
    assert script.calls == 1 and info.value.code == "payment_required"
    script = Script(httpx.Response(402, text="no body"))
    with pytest.raises(SpendLimitError, match="HTTP 402") as info:
        run_async(_gemini(mock_client, fake_api_key, fake_sleep, script).extract(DOC))
    assert info.value.code == "Payment Required"


def test_plain_429_is_still_retried(
    mock_client: Any,
    fake_api_key: str,
    fake_sleep: Any,
    gemini_envelope: Any,
    invoice_json: Any,
    run_async: Any,
) -> None:
    body = {"error": {"code": "rate_limit_exceeded", "status": "RESOURCE_EXHAUSTED"}}
    script = Script(
        httpx.Response(429, headers={"Retry-After": "3"}, json=body),
        httpx.Response(200, json=gemini_envelope(invoice_json())),
    )
    response = run_async(_gemini(mock_client, fake_api_key, fake_sleep, script).extract(DOC))
    assert script.calls == 2 and fake_sleep.delays == [3.0]
    assert response.usage.status == "ok"


def test_408_is_retried(
    mock_client: Any,
    fake_api_key: str,
    fake_sleep: Any,
    gemini_envelope: Any,
    invoice_json: Any,
    run_async: Any,
) -> None:
    assert 408 in RETRY_STATUSES
    script = Script(httpx.Response(408), httpx.Response(200, json=gemini_envelope(invoice_json())))
    response = run_async(_gemini(mock_client, fake_api_key, fake_sleep, script).extract(DOC))
    assert script.calls == 2 and [r.status for r in response.attempts] == ["provider_error", "ok"]


@pytest.mark.parametrize("status", [302, 304])
def test_statuses_outside_2xx_success_are_provider_errors(
    mock_client: Any, fake_api_key: str, fake_sleep: Any, run_async: Any, status: int
) -> None:
    script = Script(
        httpx.Response(status, headers={"location": "https://elsewhere"}), httpx.Response(200)
    )
    with pytest.raises(ProviderError, match=f"HTTP {status}"):
        run_async(_openai(mock_client, fake_api_key, fake_sleep, script).extract(DOC))
    assert script.calls == 1 and fake_sleep.delays == []


def test_non_transport_httpx_errors_are_not_retried(
    mock_client: Any, fake_api_key: str, fake_sleep: Any, run_async: Any
) -> None:
    script = Script(httpx.DecodingError("bad gzip"), httpx.Response(200))
    with pytest.raises(ProviderError, match="DecodingError") as info:
        run_async(_openai(mock_client, fake_api_key, fake_sleep, script).extract(DOC))
    assert script.calls == 1 and fake_sleep.delays == []
    (record,) = info.value.attempts
    assert record.status == "provider_error" and record.http_status is None


def test_transport_errors_are_still_retried(
    mock_client: Any, fake_api_key: str, fake_sleep: Any, run_async: Any
) -> None:
    script = Script(
        httpx.ReadError("reset"), httpx.ConnectError("refused"), httpx.WriteTimeout("slow")
    )
    with pytest.raises(ProviderUnavailableError) as info:
        run_async(_openai(mock_client, fake_api_key, fake_sleep, script).extract(DOC))
    assert script.calls == 3
    assert [r.status for r in info.value.attempts] == [
        "provider_error",
        "provider_error",
        "timeout",
    ]


def test_limit_code_table_matches_documentation() -> None:
    assert SPEND_LIMIT_CODES == {
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "credit_balance_exhausted",
        "payment_required",
    }
    assert not is_spend_limit(httpx.Response(429, json={"error": {"code": "slow_down"}}))
    assert not is_spend_limit(
        httpx.Response(429, json={"error": {"type": "credit_balance_exhausted"}})
    )
    assert not is_spend_limit(
        httpx.Response(200, json={"error": {"code": "credit_balance_exhausted"}})
    )
