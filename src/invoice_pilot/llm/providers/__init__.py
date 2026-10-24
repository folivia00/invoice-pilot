"""Provider interface and factory. Concrete providers: ``gemini``, ``openai``."""

import asyncio
import random
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

import httpx

from invoice_pilot.llm.config import LLMConfig
from invoice_pilot.llm.http import JitterFn, SleepFn
from invoice_pilot.llm.types import DocumentInput, ProviderResponse


@runtime_checkable
class Provider(Protocol):
    """One model behind one API. Implementations never log or expose the API key."""

    name: str
    model: str

    @property
    def params(self) -> Mapping[str, str]:
        """Parameters that change the answer (part of the recording key)."""
        ...

    async def extract(self, doc: DocumentInput) -> ProviderResponse: ...


def make_provider(
    config: LLMConfig,
    *,
    api_key: str,
    client: httpx.AsyncClient,
    sleep: SleepFn = asyncio.sleep,
    jitter: JitterFn = random.random,
) -> Provider:
    """Build the provider selected by ``config``."""
    if config.provider == "gemini":
        from invoice_pilot.llm.providers.gemini import GeminiProvider

        return GeminiProvider(
            api_key=api_key,
            model=config.model,
            client=client,
            thinking_level=config.thinking_level,
            media_resolution=config.media_resolution,
            max_output_tokens=config.max_output_tokens,
            sleep=sleep,
            jitter=jitter,
        )
    if config.provider == "openai":
        from invoice_pilot.llm.providers.openai import OpenAIProvider

        return OpenAIProvider(
            api_key=api_key,
            model=config.model,
            client=client,
            image_detail=config.image_detail,
            reasoning_effort=config.reasoning_effort,
            max_output_tokens=config.max_output_tokens,
            sleep=sleep,
            jitter=jitter,
        )
    raise ValueError(f"unknown provider {config.provider!r}")
