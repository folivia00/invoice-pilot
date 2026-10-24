"""Shared HTTP layer: timeouts, bounded retries with backoff, typed failures.

Retry policy (both providers document "retry with exponential backoff"):
    retried   : HTTP 429, 500, 502, 503, 504, timeouts, transport errors
    not retried: any other 4xx -> ProviderError at once
    attempts  : MAX_ATTEMPTS in total; delay = base * 2**(n-1) * (0.5 + jitter),
                capped, and at least Retry-After (seconds, capped) when present
The sleep function is injected so tests run without waiting.
"""

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

import httpx

from invoice_pilot.llm.errors import ProviderError, ProviderUnavailableError
from invoice_pilot.llm.types import CallStatus, UsageRecord

CONNECT_TIMEOUT_S: Final = 10.0
READ_TIMEOUT_S: Final = 60.0
TIMEOUT: Final = httpx.Timeout(
    connect=CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S, write=30.0, pool=10.0
)

MAX_ATTEMPTS: Final = 3
RETRY_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
BASE_DELAY_S: Final = 1.0
MAX_DELAY_S: Final = 20.0
RETRY_AFTER_CAP_S: Final = 30.0

SleepFn = Callable[[float], Awaitable[None]]
JitterFn = Callable[[], float]  # uniform in [0, 1)


@dataclass(frozen=True, slots=True)
class Attempt:
    status: CallStatus
    http_status: int | None
    latency_ms: int


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


def error_code(response: httpx.Response) -> str:
    """Short machine-readable code from a JSON error body, or the HTTP status text."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        error = None
    if isinstance(error, dict):
        for key in ("status", "code", "type"):
            value = error.get(key)
            if isinstance(value, str | int) and str(value):
                return str(value)
    return response.reason_phrase or "error"


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
        except httpx.HTTPError as exc:
            records.append(_record(provider, model, "provider_error", None, started))
            last_failure = f"network error ({type(exc).__name__})"
        else:
            status = response.status_code
            if status < 400:
                return response, records
            code = error_code(response)
            if status in RETRY_STATUSES:
                records.append(_record(provider, model, "provider_error", status, started))
                last_failure = f"HTTP {status} {code}"
                retry_after = parse_retry_after(response.headers.get("retry-after"))
            else:
                records.append(_record(provider, model, "provider_error", status, started))
                raise ProviderError(f"{provider}: HTTP {status} {code}", tuple(records))
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
