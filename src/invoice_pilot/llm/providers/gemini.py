"""Gemini Developer API provider (REST ``models.generateContent``).

Request shape (v1beta discovery document revision 20261023, checked 2026-10-24):
    POST {base}/models/{model}:generateContent, header ``x-goog-api-key``
    systemInstruction.parts[].text          prompt
    contents[0].parts[]                     {"text": ...} or {"inlineData": {"mimeType", "data"}}
    generationConfig.responseFormat.text    {"mimeType": "APPLICATION_JSON", "schema": ...}
    generationConfig.thinkingConfig         {"thinkingLevel": MINIMAL|LOW|MEDIUM|HIGH}
    generationConfig.mediaResolution        MEDIA_RESOLUTION_LOW|MEDIUM|HIGH (optional)
Usage comes from ``usageMetadata`` (promptTokenCount, candidatesTokenCount,
thoughtsTokenCount); thinking tokens are billed as output. ``temperature`` is
not sent: it is ignored by Gemini 3.6+ and Google recommends the default.
"""

import asyncio
import random
import time
from collections.abc import Mapping
from typing import Any, Final

import httpx

from invoice_pilot.llm.errors import InvalidResponseError
from invoice_pilot.llm.http import JitterFn, SleepFn, latency_ms, post_with_retries
from invoice_pilot.llm.prompts import load_prompt
from invoice_pilot.llm.providers._common import (
    as_int,
    dict_field,
    encode_base64,
    json_envelope,
    list_of_dicts,
)
from invoice_pilot.llm.schema import INVOICE_SCHEMA
from invoice_pilot.llm.types import DocumentInput, ProviderResponse, UsageRecord, mark_invalid

GEMINI_BASE_URL: Final = "https://generativelanguage.googleapis.com/v1beta"

THINKING_LEVELS: Final = ("minimal", "low", "medium", "high")
#: Lowest thinking level each model accepts (model pages, checked 2026-10-24):
#: 3.5 Flash-Lite supports minimal (its default); 3.8 Flash rejects minimal.
MIN_THINKING_LEVEL: Final[dict[str, str]] = {
    "gemini-3.5-flash-lite": "minimal",
    "gemini-3.8-flash": "low",
}
FALLBACK_MIN_THINKING_LEVEL: Final = "low"

MEDIA_RESOLUTIONS: Final[dict[str, str]] = {
    "low": "MEDIA_RESOLUTION_LOW",
    "medium": "MEDIA_RESOLUTION_MEDIUM",
    "high": "MEDIA_RESOLUTION_HIGH",
}

USER_LEAD: Final = "Extract the invoice fields from the following document."

#: The only Candidate.finishReason that means a complete answer (v1beta discovery
#: document, revision 20261023, checked 2026-10-24): STOP = "Natural stop point of the
#: model or provided stop sequence". Everything else (MAX_TOKENS, SAFETY, RECITATION,
#: PROHIBITED_CONTENT, MALFORMED_RESPONSE, ...) makes the answer unusable.
FINISH_REASON_OK: Final = "STOP"


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: httpx.AsyncClient,
        thinking_level: str | None = None,
        media_resolution: str | None = None,
        max_output_tokens: int = 8192,
        sleep: SleepFn = asyncio.sleep,
        jitter: JitterFn = random.random,
        base_url: str = GEMINI_BASE_URL,
    ) -> None:
        if not api_key:
            raise ValueError("Gemini API key is empty")
        level = thinking_level or MIN_THINKING_LEVEL.get(model, FALLBACK_MIN_THINKING_LEVEL)
        if level not in THINKING_LEVELS:
            raise ValueError(f"unknown thinking level {level!r}")
        if media_resolution is not None and media_resolution not in MEDIA_RESOLUTIONS:
            raise ValueError(f"unknown media resolution {media_resolution!r}")
        self._api_key = api_key
        self._client = client
        self._sleep = sleep
        self._jitter = jitter
        self.model = model
        self.thinking_level = level
        self.media_resolution = media_resolution
        self.max_output_tokens = max_output_tokens
        self.url = f"{base_url}/models/{model}:generateContent"

    def __repr__(self) -> str:
        return (
            f"GeminiProvider(model={self.model!r}, thinking_level={self.thinking_level!r}, "
            f"media_resolution={self.media_resolution!r})"
        )

    @property
    def params(self) -> Mapping[str, str]:
        return {
            "thinking_level": self.thinking_level,
            "media_resolution": self.media_resolution or "default",
        }

    def request_body(self, doc: DocumentInput) -> dict[str, Any]:
        """JSON body for ``generateContent`` (public so tests can inspect it)."""
        parts: list[dict[str, Any]] = [{"text": USER_LEAD}]
        if doc.is_text:
            parts.append({"text": doc.text})
        else:
            parts.append({"inlineData": {"mimeType": doc.mime, "data": encode_base64(doc)}})
        generation: dict[str, Any] = {
            "responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": INVOICE_SCHEMA}},
            "thinkingConfig": {"thinkingLevel": self.thinking_level.upper()},
            "maxOutputTokens": self.max_output_tokens,
        }
        if self.media_resolution is not None and not doc.is_text:
            generation["mediaResolution"] = MEDIA_RESOLUTIONS[self.media_resolution]
        return {
            "systemInstruction": {"parts": [{"text": load_prompt()}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation,
        }

    async def extract(self, doc: DocumentInput) -> ProviderResponse:
        started = time.perf_counter()
        response, failed = await post_with_retries(
            self._client,
            self.url,
            headers={"x-goog-api-key": self._api_key},
            body=self.request_body(doc),
            provider=self.name,
            model=self.model,
            sleep=self._sleep,
            jitter=self._jitter,
        )
        return self._parse(response, failed, started)

    def _parse(
        self, response: httpx.Response, failed: list[UsageRecord], started: float
    ) -> ProviderResponse:
        name = self.name
        payload = json_envelope(response, name, failed)
        meta = dict_field(payload.get("usageMetadata"), "usageMetadata", name, failed)
        thoughts = as_int(meta.get("thoughtsTokenCount"))
        usage = UsageRecord(
            provider=name,
            model=self.model,
            status="ok",
            input_tokens=as_int(meta.get("promptTokenCount")),
            output_tokens=as_int(meta.get("candidatesTokenCount")) + thoughts,
            thinking_tokens=thoughts,
            latency_ms=latency_ms(started),
            http_status=response.status_code,
        )
        attempts = (*failed, usage)
        feedback = dict_field(payload.get("promptFeedback"), "promptFeedback", name, attempts)
        if feedback.get("blockReason"):
            reason = feedback["blockReason"]
            raise InvalidResponseError(f"gemini: prompt blocked ({reason})", mark_invalid(attempts))
        candidates = list_of_dicts(payload.get("candidates"), "candidates", name, attempts)
        if not candidates:
            raise InvalidResponseError("gemini: no candidates in response", mark_invalid(attempts))
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        if finish is not None and finish != FINISH_REASON_OK:
            raise InvalidResponseError(
                f"gemini: generation stopped (finishReason={finish})", mark_invalid(attempts)
            )
        content = dict_field(candidate.get("content"), "candidate content", name, attempts)
        parts = list_of_dicts(content.get("parts"), "content parts", name, attempts)
        text = "".join(
            part["text"]
            for part in parts
            if isinstance(part.get("text"), str) and not part.get("thought")
        )
        if not text.strip():
            raise InvalidResponseError(
                f"gemini: empty answer (finishReason={finish or 'missing'})", mark_invalid(attempts)
            )
        return ProviderResponse(raw_text=text, usage=usage, attempts=attempts)
