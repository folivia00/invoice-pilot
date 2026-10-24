"""OpenAI Responses API provider.

Request shape (API reference, checked 2026-10-24):
    POST https://api.openai.com/v1/responses, header ``Authorization: Bearer``
    instructions                      prompt
    input[0].content[]                input_text | input_file (file_data data URL, filename)
                                      | input_image (image_url data URL, detail)
    text.format                       {"type": "json_schema", "name", "schema", "strict": true}
    reasoning.effort                  optional (gpt-6-luna accepts "none")
    store                             false (nothing kept on the provider side)
A refusal arrives as an output content part of type ``refusal``; a response
with status other than ``completed`` carries ``incomplete_details.reason``.
Usage: ``usage.input_tokens``, ``usage.output_tokens`` (includes reasoning),
``usage.output_tokens_details.reasoning_tokens``.
"""

import asyncio
import random
import time
from collections.abc import Mapping
from typing import Any, Final

import httpx

from invoice_pilot.llm.errors import InvalidResponseError, ProviderError
from invoice_pilot.llm.http import JitterFn, SleepFn, latency_ms, post_with_retries
from invoice_pilot.llm.prompts import load_prompt
from invoice_pilot.llm.providers._common import as_int, encode_base64
from invoice_pilot.llm.schema import INVOICE_SCHEMA, SCHEMA_NAME
from invoice_pilot.llm.types import (
    MIME_PDF,
    DocumentInput,
    ProviderResponse,
    UsageRecord,
    mark_invalid,
)

OPENAI_RESPONSES_URL: Final = "https://api.openai.com/v1/responses"
IMAGE_DETAILS: Final = ("auto", "low", "high", "original")
REASONING_EFFORTS: Final = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
USER_LEAD: Final = "Extract the invoice fields from the following document."


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: httpx.AsyncClient,
        image_detail: str = "auto",
        reasoning_effort: str | None = "none",
        max_output_tokens: int = 8192,
        sleep: SleepFn = asyncio.sleep,
        jitter: JitterFn = random.random,
        url: str = OPENAI_RESPONSES_URL,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key is empty")
        if image_detail not in IMAGE_DETAILS:
            raise ValueError(f"unknown image detail {image_detail!r}")
        if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORTS:
            raise ValueError(f"unknown reasoning effort {reasoning_effort!r}")
        self._api_key = api_key
        self._client = client
        self._sleep = sleep
        self._jitter = jitter
        self.model = model
        self.image_detail = image_detail
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        self.url = url

    def __repr__(self) -> str:
        return (
            f"OpenAIProvider(model={self.model!r}, image_detail={self.image_detail!r}, "
            f"reasoning_effort={self.reasoning_effort!r})"
        )

    @property
    def params(self) -> Mapping[str, str]:
        return {
            "image_detail": self.image_detail,
            "reasoning_effort": self.reasoning_effort or "default",
        }

    def request_body(self, doc: DocumentInput) -> dict[str, Any]:
        """JSON body for ``POST /v1/responses`` (public so tests can inspect it)."""
        content: list[dict[str, Any]] = [{"type": "input_text", "text": USER_LEAD}]
        if doc.is_text:
            content.append({"type": "input_text", "text": doc.text})
        elif doc.mime == MIME_PDF:
            content.append(
                {
                    "type": "input_file",
                    "filename": "document.pdf",
                    "file_data": f"data:{MIME_PDF};base64,{encode_base64(doc)}",
                }
            )
        else:
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{doc.mime};base64,{encode_base64(doc)}",
                    "detail": self.image_detail,
                }
            )
        body: dict[str, Any] = {
            "model": self.model,
            "instructions": load_prompt(),
            "input": [{"role": "user", "content": content}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": SCHEMA_NAME,
                    "schema": INVOICE_SCHEMA,
                    "strict": True,
                }
            },
            "store": False,
            "max_output_tokens": self.max_output_tokens,
        }
        if self.reasoning_effort is not None:
            body["reasoning"] = {"effort": self.reasoning_effort}
        return body

    async def extract(self, doc: DocumentInput) -> ProviderResponse:
        started = time.perf_counter()
        response, failed = await post_with_retries(
            self._client,
            self.url,
            headers={"authorization": f"Bearer {self._api_key}"},
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
        try:
            payload = response.json()
        except ValueError:
            raise ProviderError("openai: response is not JSON", tuple(failed)) from None
        if not isinstance(payload, dict):
            raise ProviderError("openai: unexpected response envelope", tuple(failed))
        meta = payload.get("usage") or {}
        reasoning = as_int((meta.get("output_tokens_details") or {}).get("reasoning_tokens"))
        usage = UsageRecord(
            provider=self.name,
            model=self.model,
            status="ok",
            input_tokens=as_int(meta.get("input_tokens")),
            output_tokens=as_int(meta.get("output_tokens")),
            thinking_tokens=reasoning,
            latency_ms=latency_ms(started),
            http_status=response.status_code,
        )
        attempts = (*failed, usage)
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code") or error.get("type") or "error"
            raise ProviderError(f"openai: response error ({code})", attempts)
        status = payload.get("status")
        if status != "completed":
            reason = (payload.get("incomplete_details") or {}).get("reason", "unknown")
            raise InvalidResponseError(
                f"openai: response status {status} ({reason})", mark_invalid(attempts)
            )
        texts: list[str] = []
        for item in payload.get("output") or []:
            if item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                kind = part.get("type")
                if kind == "refusal":
                    raise InvalidResponseError("openai: model refused", mark_invalid(attempts))
                if kind == "output_text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
        text = "".join(texts)
        if not text.strip():
            raise InvalidResponseError("openai: empty answer", mark_invalid(attempts))
        return ProviderResponse(raw_text=text, usage=usage, attempts=attempts)
