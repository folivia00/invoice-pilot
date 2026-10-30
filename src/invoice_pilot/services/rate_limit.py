"""Global sliding-window limit on provider HTTP calls (Gemini free tier: 15 RPM).

Every HTTP attempt takes one slot, retries included. The HTTP layer of ``llm``
retries inside ``provider.extract`` and calls the injected ``sleep`` exactly
once before each further attempt. So the limit is enforced at two points
without changing ``llm``:

* ``RateLimitedProvider.extract`` takes a slot before the first attempt;
* ``limited_sleep`` (passed to ``make_provider`` as ``sleep``) waits the backoff,
  then takes a slot for the attempt that follows.

Waiting is never an error: a caller over the limit sleeps until the oldest call
in the window is older than the window.
"""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from typing import Final

from invoice_pilot.llm.providers import Provider
from invoice_pilot.llm.types import DocumentInput, ProviderResponse

WINDOW_S: Final = 60.0

Monotonic = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class SlidingWindowLimiter:
    """At most ``max_calls`` acquisitions in any ``window_s`` seconds (FIFO for waiters)."""

    def __init__(
        self,
        max_calls: int,
        *,
        monotonic: Monotonic,
        sleep: Sleep = asyncio.sleep,
        window_s: float = WINDOW_S,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be at least 1")
        self.max_calls = max_calls
        self.window_s = window_s
        self._monotonic = monotonic
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()
        #: Total seconds callers spent waiting (for tests and diagnostics).
        self.waited_s = 0.0

    def _purge(self, now: float) -> None:
        while self._calls and self._calls[0] <= now - self.window_s:
            self._calls.popleft()

    async def acquire(self) -> None:
        """Take one slot, waiting while the window is full."""
        async with self._lock:
            while True:
                now = self._monotonic()
                self._purge(now)
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                wait = self._calls[0] + self.window_s - now
                self.waited_s += wait
                await self._sleep(wait)


def limited_sleep(limiter: SlidingWindowLimiter, sleep: Sleep = asyncio.sleep) -> Sleep:
    """Backoff sleep for the HTTP layer that also takes a slot for the next attempt."""

    async def _sleep(seconds: float) -> None:
        await sleep(seconds)
        await limiter.acquire()

    return _sleep


class RateLimitedProvider:
    """Provider wrapper that takes a limiter slot before the first HTTP attempt."""

    def __init__(self, inner: Provider, limiter: SlidingWindowLimiter) -> None:
        self._inner = inner
        self._limiter = limiter
        self.name = inner.name
        self.model = inner.model

    def __repr__(self) -> str:
        return f"RateLimitedProvider(inner={self._inner!r})"

    @property
    def params(self) -> Mapping[str, str]:
        return self._inner.params

    async def extract(self, doc: DocumentInput) -> ProviderResponse:
        await self._limiter.acquire()
        return await self._inner.extract(doc)
