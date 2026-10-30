"""Compact queue (one document at a time, "In queue" notice) and the RPM limiter."""

import asyncio
from pathlib import Path

import httpx
from helpers_app import (
    FakeClock,
    FakeProvider,
    dataset_bytes,
    make_processor,
    open_storage,
    truth_answer,
)

from invoice_pilot.llm.config import LLMConfig
from invoice_pilot.llm.providers import make_provider
from invoice_pilot.llm.types import DocumentInput
from invoice_pilot.services.processing import (
    ProcessingQueue,
    ProcessingResult,
    ResultKind,
    UploadMeta,
    queue_message,
)
from invoice_pilot.services.rate_limit import (
    RateLimitedProvider,
    SlidingWindowLimiter,
    limited_sleep,
)

PDF = dataset_bytes("inv-001")
META = UploadMeta("document", "application/pdf", "inv.pdf", len(PDF))


def test_second_user_waits_in_queue_then_gets_result(db_path: Path, clock: FakeClock) -> None:
    async def scenario() -> tuple[list[str], list[str], list[ProcessingResult], int]:
        async with open_storage(db_path) as storage:
            gate = asyncio.Event()
            provider = FakeProvider(truth_answer("inv-001"), gate=gate)
            processor = make_processor(storage, provider, clock, concurrency=1)
            notices: dict[int, list[str]] = {1: [], 2: []}

            async def run(user: int) -> ProcessingResult:
                async def download() -> bytes:
                    return PDF

                async def notify(text: str) -> None:
                    notices[user].append(text)

                return await processor.process(user, META, download, notify)

            first = asyncio.create_task(run(1))
            await provider.started.wait()  # user 1 is at the model
            second = asyncio.create_task(run(2))
            for _ in range(50):
                if notices[2]:
                    break
                await asyncio.sleep(0.01)
            assert provider.calls == 1  # user 2 has not reached the model
            gate.set()
            results = list(await asyncio.gather(first, second))
            return notices[1], notices[2], results, provider.max_active

    notices_1, notices_2, results, max_active = asyncio.run(scenario())
    assert notices_1 == []
    assert notices_2 == [queue_message(1)]
    assert notices_2[0] == "In queue (position 1). I'll reply when it's your turn."
    assert [r.kind for r in results] == [ResultKind.DONE, ResultKind.DONE]
    assert max_active == 1


def test_queue_positions_and_concurrency_one() -> None:
    async def scenario() -> tuple[list[int], int]:
        queue = ProcessingQueue(1)
        positions: list[int] = []
        release = asyncio.Event()

        async def notice(position: int) -> None:
            positions.append(position)

        async def job() -> None:
            async with queue.slot(notice):
                await release.wait()

        tasks = [asyncio.create_task(job()) for _ in range(4)]
        while len(positions) < 3:
            await asyncio.sleep(0)
        release.set()
        await asyncio.gather(*tasks)
        return positions, queue.max_active

    positions, max_active = asyncio.run(scenario())
    assert positions == [1, 2, 3]
    assert max_active == 1


def test_queue_full_profile_runs_in_parallel() -> None:
    async def scenario() -> int:
        queue = ProcessingQueue(3)
        gate = asyncio.Event()

        async def notice(position: int) -> None:
            raise AssertionError("no waiting expected")

        async def job() -> None:
            async with queue.slot(notice):
                await gate.wait()

        tasks = [asyncio.create_task(job()) for _ in range(3)]
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(*tasks)
        return queue.max_active

    assert asyncio.run(scenario()) == 3


def test_failed_queue_notice_keeps_the_place() -> None:
    async def scenario() -> int:
        queue = ProcessingQueue(1)
        gate = asyncio.Event()
        done = []

        async def broken(position: int) -> None:
            raise RuntimeError("telegram is down")

        async def job(name: str) -> None:
            async with queue.slot(broken):
                await gate.wait()
                done.append(name)

        tasks = [asyncio.create_task(job("a")), asyncio.create_task(job("b"))]
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(*tasks)
        return len(done)

    assert asyncio.run(scenario()) == 2


def test_rpm_limit_fourth_call_waits_for_the_window() -> None:
    clock = FakeClock()

    async def scenario() -> list[float]:
        limiter = SlidingWindowLimiter(3, monotonic=clock.monotonic, sleep=clock.sleep)
        times = []
        for _ in range(3):
            await limiter.acquire()
            times.append(clock.mono)
            clock.advance(5)
        await limiter.acquire()
        times.append(clock.mono)
        return times

    start = clock.mono
    times = asyncio.run(scenario())
    assert [t - start for t in times] == [0, 5, 10, 60]
    assert clock.sleeps == [45]  # waited until the first call left the window


def test_rate_limiter_counts_every_http_attempt_including_retries(
    gemini_envelope: object, invoice_json: object
) -> None:
    """Real Gemini provider, 429 twice then 200: three HTTP calls take three slots."""
    clock = FakeClock()
    statuses = [429, 429, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        status = statuses.pop(0)
        if status == 200:
            return httpx.Response(200, json=gemini_envelope(invoice_json()))  # type: ignore[operator]
        return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}})

    async def scenario() -> tuple[int, list[float]]:
        limiter = SlidingWindowLimiter(2, monotonic=clock.monotonic, sleep=clock.sleep)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        inner = make_provider(
            LLMConfig(provider="gemini", model="gemini-3.5-flash-lite"),
            api_key="fake-key",
            client=client,
            sleep=limited_sleep(limiter, sleep=clock.sleep),
            jitter=lambda: 0.5,
        )
        provider = RateLimitedProvider(inner, limiter)
        response = await provider.extract(DocumentInput(text="invoice text"))
        await client.aclose()
        return len(response.attempts), clock.sleeps

    attempts, sleeps = asyncio.run(scenario())
    assert attempts == 3
    # backoff 1 s, slot 2 free; backoff 2 s, window (2 calls) full -> wait until 60 s
    assert sleeps[:2] == [1.0, 2.0]
    assert sum(sleeps) == 60.0


def test_rate_limited_provider_keeps_identity() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(1, monotonic=clock.monotonic, sleep=clock.sleep)
    wrapped = RateLimitedProvider(FakeProvider("{}"), limiter)
    assert (wrapped.name, wrapped.model, dict(wrapped.params)) == (
        "gemini",
        "gemini-3.5-flash-lite",
        {},
    )
