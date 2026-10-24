"""Shared test configuration: Hypothesis profiles, network guard, LLM test helpers.

The default Hypothesis profile ("ci") is deterministic (derandomize=True) and
uses no example database, so CI and local runs explore the same examples and
leave no .hypothesis/ directory behind. Select another profile with
HYPOTHESIS_PROFILE=dev|thorough for randomized exploration.

No test may reach the network: the real httpx transports are disabled for every
test, so only ``httpx.MockTransport`` clients work.
"""

import asyncio
import json
import os
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
from hypothesis import settings

settings.register_profile("ci", derandomize=True, max_examples=200, deadline=None, database=None)
settings.register_profile("dev", max_examples=100, deadline=None)
settings.register_profile("thorough", max_examples=2000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))

FAKE_API_KEY = "test-key-SECRET-0123456789abcdef"


class NetworkAccessError(RuntimeError):
    pass


def _no_network(*args: object, **kwargs: object) -> object:
    raise NetworkAccessError("tests must not touch the network; use httpx.MockTransport")


@pytest.fixture(autouse=True)
def _disable_real_transports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _no_network)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _no_network)


# --- LLM helpers (fixtures so every test package can use them) ------------------------


@pytest.fixture
def run_async() -> Callable[[Any], Any]:
    return lambda coro: asyncio.run(coro)


@pytest.fixture
def fake_api_key() -> str:
    return FAKE_API_KEY


@pytest.fixture
def mock_client() -> Callable[[Callable[[httpx.Request], httpx.Response]], httpx.AsyncClient]:
    """Build an AsyncClient whose transport is the given handler."""
    return lambda handler: httpx.AsyncClient(transport=httpx.MockTransport(handler))


class FakeSleep:
    """Records requested delays instead of waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


@pytest.fixture
def fake_sleep() -> FakeSleep:
    return FakeSleep()


VALID_INVOICE: dict[str, Any] = {
    "supplier": {"name": "Acme Ltd", "tax_id": "GB123456789", "address": "1 High St, Leeds"},
    "buyer": {"name": "Bob Builder", "tax_id": None, "address": None},
    "invoice_number": "INV-1",
    "invoice_date": "2026-09-01",
    "due_date": "2026-09-15",
    "currency": "GBP",
    "line_items": [
        {"description": "Consulting, hours", "quantity": 2, "unit_price": 100, "amount": 200}
    ],
    "subtotal": 200,
    "tax_rate": 20,
    "tax_amount": 40,
    "total": 240,
    "iban": None,
    "bic": None,
}

_RAW_RE = re.compile(r'"@@RAW:([^"]*)@@"')


def dumps_with_decimals(payload: Any) -> str:
    """json.dumps that writes Decimal values as JSON numbers (not strings, not floats)."""

    def default(value: Any) -> Any:
        if isinstance(value, Decimal):
            return f"@@RAW:{value}@@"
        raise TypeError(type(value))

    return _RAW_RE.sub(r"\1", json.dumps(payload, default=default))


@pytest.fixture
def invoice_json() -> Callable[..., str]:
    """JSON text of a valid model answer, with optional top-level overrides."""

    def build(**overrides: Any) -> str:
        return dumps_with_decimals({**VALID_INVOICE, **overrides})

    return build


@pytest.fixture
def gemini_envelope() -> Callable[..., dict[str, Any]]:
    def build(
        text: str | None,
        *,
        prompt_tokens: int = 100,
        candidate_tokens: int = 50,
        thoughts_tokens: int = 20,
        finish_reason: str = "STOP",
        block_reason: str | None = None,
    ) -> dict[str, Any]:
        envelope: dict[str, Any] = {
            "usageMetadata": {
                "promptTokenCount": prompt_tokens,
                "candidatesTokenCount": candidate_tokens,
                "thoughtsTokenCount": thoughts_tokens,
                "totalTokenCount": prompt_tokens + candidate_tokens + thoughts_tokens,
            },
            "modelVersion": "test",
        }
        if block_reason:
            envelope["promptFeedback"] = {"blockReason": block_reason}
            return envelope
        parts = [] if text is None else [{"text": text}]
        envelope["candidates"] = [
            {"content": {"role": "model", "parts": parts}, "finishReason": finish_reason}
        ]
        return envelope

    return build


@pytest.fixture
def openai_envelope() -> Callable[..., dict[str, Any]]:
    def build(
        text: str | None,
        *,
        input_tokens: int = 100,
        output_tokens: int = 70,
        reasoning_tokens: int = 20,
        status: str = "completed",
        refusal: str | None = None,
        incomplete_reason: str | None = None,
    ) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if refusal is not None:
            content.append({"type": "refusal", "refusal": refusal})
        elif text is not None:
            content.append({"type": "output_text", "text": text, "annotations": []})
        envelope: dict[str, Any] = {
            "id": "resp_test",
            "object": "response",
            "status": status,
            "error": None,
            "incomplete_details": ({"reason": incomplete_reason} if incomplete_reason else None),
            "output": [
                {"type": "reasoning", "id": "rs_test", "summary": []},
                {
                    "type": "message",
                    "id": "msg_test",
                    "role": "assistant",
                    "status": "completed",
                    "content": content,
                },
            ],
            "usage": {
                "input_tokens": input_tokens,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": output_tokens,
                "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
                "total_tokens": input_tokens + output_tokens,
            },
        }
        return envelope

    return build
