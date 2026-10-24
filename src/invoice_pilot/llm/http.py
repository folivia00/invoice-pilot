"""Shared HTTP layer: timeouts, bounded retries with backoff, typed failures.

Retry policy (both providers document "retry with exponential backoff"):
    retried    : HTTP 408, 429 (rate limit), 500, 502, 503, 504 and ``httpx.TransportError``
                 (timeouts, connection and read errors)
    not retried: 402 and a 429 whose ``error.code`` is a spend/credit limit -> SpendLimitError;
                 any other status outside 2xx (3xx, other 4xx) -> ProviderError;
                 other httpx errors (decoding and the like) -> ProviderError
    attempts   : MAX_ATTEMPTS in total; delay = base * 2**(n-1) * (0.5 + jitter),
                 capped, and at least Retry-After (seconds, capped) when present
The sleep function is injected so tests run without waiting.
"""

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, Final

import httpx

from invoice_pilot.llm.errors import ProviderError, ProviderUnavailableError, SpendLimitError
from invoice_pilot.llm.types import CallStatus, UsageRecord

CONNECT_TIMEOUT_S: Final = 10.0
READ_TIMEOUT_S: Final = 60.0
TIMEOUT: Final = httpx.Timeout(
    connect=CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S, write=30.0, pool=10.0
)

MAX_ATTEMPTS: Final = 3
RETRY_STATUSES: Final[frozenset[int]] = frozenset({408, 429, 500, 502, 503, 504})
BASE_DELAY_S: Final = 1.0
MAX_DELAY_S: Final = 20.0
RETRY_AFTER_CAP_S: Final = 30.0

#: ``error.code`` values that mean "no money or allowance left": retrying cannot help.
#: OpenAI (spend-limits and error-codes guides, checked 2026-10-24): HTTP 429 with these
#: codes. Gemini (api-errors, checked 2026-10-24): HTTP 402 ``payment_required``.
SPEND_LIMIT_CODES: Final[frozenset[str]] = frozenset(
    {
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "credit_balance_exhausted",
        "payment_required",
    }
)

SleepFn = Callable[[float], Awaitable[None]]
JitterFn = Callable[[], float]  # uniform in [0, 1)


def retry_delay(attempt: int, retry_after_s: float | None, jitter: float) -> float:
    """Seconds to wait after failed attempt number ``attempt`` (1-based)."""
    delay = min(BASE_DELAY_S * 2 ** (attempt - 1), MAX_DELAY_S) * (0.5 + jitter)
    if retry_after_s is not None:
        delay = max(delay, min(retry_after_s, RETRY_AFTER_CAP_S))
    return delay


def parse_retry_after(value: str | None) -> float | None:
    """``Retry-After`` in seconds; the HTTP-date form is ignored (treated as absent)."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _error_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    error = payload.get("error") if isinstance(payload, dict) else None
    return error if isinstance(error, dict) else {}


def error_code_field(response: httpx.Response) -> str | None:
    """The ``error.code`` string of a JSON error body, if any (used for spend limits)."""
    code = _error_object(response).get("code")
    return code if isinstance(code, str) and code else None


def error_code(response: httpx.Response) -> str:
    """Short machine-readable code for messages: error.status, .code or .type, else reason."""
    error = _error_object(response)
    for key in ("status", "code", "type"):
        value = error.get(key)
        if isinstance(value, str | int) and not isinstance(value, bool) and str(value):
            return str(value)
    return response.reason_phrase or "error"


def is_spend_limit(response: httpx.Response) -> bool:
    """402, or 429 whose ``error.code`` names a spend, usage or credit limit."""
    if response.status_code == 402:
        return True
    return response.status_code == 429 and error_code_field(response) in SPEND_LIMIT_CODES


async def post_with_retries(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
    provider: str,
    model: str,
    sleep: SleepFn,
    jitter: JitterFn,
) -> tuple[httpx.Response, list[UsageRecord]]:
    """POST ``body`` as JSON, retrying transient failures; return the 2xx response.

    The body is serialized exactly once and the same bytes are reused on retries.
    Returned records cover the failed attempts; the caller appends the final one
    after reading token usage from the response.
    """
    content = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    del body
    headers = {**headers, "content-type": "application/json"}
    records: list[UsageRecord] = []
    last_failure = "no attempt made"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.perf_counter()
        retry_after: float | None = None
        try:
            response = await client.post(url, headers=headers, content=content, timeout=TIMEOUT)
        except httpx.TimeoutException:
            records.append(_record(provider, model, "timeout", None, started))
            last_failure = "timeout"
        except httpx.TransportError as exc:
            records.append(_record(provider, model, "provider_error", None, started))
            last_failure = f"network error ({type(exc).__name__})"
        except httpx.HTTPError as exc:
            records.append(_record(provider, model, "provider_error", None, started))
            raise ProviderError(
                f"{provider}: request failed ({type(exc).__name__})", tuple(records)
            ) from None
        else:
            status = response.status_code
            if 200 <= status < 300:
                return response, records
            records.append(_record(provider, model, "provider_error", status, started))
            code = error_code(response)
            if is_spend_limit(response):
                limit_code = error_code_field(response) or code
                raise SpendLimitError(
                    f"{provider}: spend limit reached (HTTP {status} {limit_code})",
                    tuple(records),
                    code=limit_code,
                )
            if status not in RETRY_STATUSES:
                raise ProviderError(f"{provider}: HTTP {status} {code}", tuple(records))
            last_failure = f"HTTP {status} {code}"
            retry_after = parse_retry_after(response.headers.get("retry-after"))
        if attempt < MAX_ATTEMPTS:
            await sleep(retry_delay(attempt, retry_after, jitter()))
    raise ProviderUnavailableError(
        f"{provider}: unavailable after {MAX_ATTEMPTS} attempts (last: {last_failure})",
        tuple(records),
    )


def latency_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _record(
    provider: str, model: str, status: CallStatus, http_status: int | None, started: float
) -> UsageRecord:
    return UsageRecord(
        provider=provider,
        model=model,
        status=status,
        latency_ms=latency_ms(started),
        http_status=http_status,
    )
