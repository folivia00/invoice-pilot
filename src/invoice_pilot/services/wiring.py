"""Shared wiring of the bot and API processes: provider, limits, queue, processor.

Each process builds its own processor over its own database; the locks, the
queue and the rate limiter live in that process (one process per database).
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from invoice_pilot.config import Settings
from invoice_pilot.extraction.prepare import InputLimits
from invoice_pilot.llm.config import LLMConfig
from invoice_pilot.llm.pricing import cost_usd
from invoice_pilot.llm.providers import Provider, make_provider
from invoice_pilot.services.fake_llm import FakeProvider, zero_cost
from invoice_pilot.services.limits import DailyLimits
from invoice_pilot.services.processing import DocumentProcessor, Pricing, ProcessingQueue
from invoice_pilot.services.rate_limit import (
    RateLimitedProvider,
    SlidingWindowLimiter,
    limited_sleep,
)
from invoice_pilot.storage import Storage

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def build_provider(settings: Settings, client: httpx.AsyncClient) -> tuple[Provider, Pricing]:
    """The rate-limited Gemini provider, or the fake one when ``LLM_FAKE`` is on."""
    if settings.llm_fake:
        logger.warning("LLM_FAKE is on: built-in synthetic answers, no model is called")
        return FakeProvider(), zero_cost
    limiter = SlidingWindowLimiter(settings.llm_rpm, monotonic=time.monotonic)
    inner = make_provider(
        LLMConfig(provider="gemini", model=settings.llm_model),
        api_key=settings.gemini_api_key,
        client=client,
        sleep=limited_sleep(limiter),
    )
    return RateLimitedProvider(inner, limiter), cost_usd


def build_processor(
    settings: Settings,
    storage: Storage,
    client: httpx.AsyncClient,
    *,
    user_daily_docs: int,
    clock: Clock = utc_now,
) -> DocumentProcessor:
    """Processor with the daily limits and the queue of one process."""
    provider, pricing = build_provider(settings, client)
    limits = DailyLimits(
        storage,
        user_daily_docs=user_daily_docs,
        global_daily_docs=settings.global_daily_docs,
        global_daily_budget_usd=settings.global_daily_budget_usd,
        clock=clock,
    )
    return DocumentProcessor(
        storage=storage,
        limits=limits,
        provider=provider,
        queue=ProcessingQueue(settings.concurrency, settings.queue_max),
        input_limits=InputLimits(max_bytes=settings.max_file_bytes, max_pages=settings.max_pages),
        clock=clock,
        pricing=pricing,
    )
