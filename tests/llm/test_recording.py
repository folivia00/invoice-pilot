"""Record / replay wrapper: stable keys, file content, no network in replay."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from invoice_pilot.llm.errors import InvalidResponseError, RecordingMissingError
from invoice_pilot.llm.prompts import PROMPT_VERSION
from invoice_pilot.llm.providers.gemini import GeminiProvider
from invoice_pilot.llm.recording import RecordingProvider, recording_key
from invoice_pilot.llm.schema import schema_sha256
from invoice_pilot.llm.types import DocumentInput, ProviderResponse, UsageRecord

DOC = DocumentInput(data=b"%PDF-1.4 sample", mime="application/pdf")
TEXT_DOC = DocumentInput(text="Invoice 1")


class FakeProvider:
    name = "gemini"
    model = "gemini-test"

    def __init__(self, raw_text: str = '{"ok": true}', fail: Exception | None = None) -> None:
        self.raw_text = raw_text
        self.fail = fail
        self.calls = 0
        self.params = {"thinking_level": "minimal", "media_resolution": "default"}

    async def extract(self, doc: DocumentInput) -> ProviderResponse:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        usage = UsageRecord(self.name, self.model, "ok", 120, 40, 10, 321, 200)
        return ProviderResponse(raw_text=self.raw_text, usage=usage, attempts=(usage,))


def test_key_is_stable_and_documented() -> None:
    params = {"media_resolution": "default", "thinking_level": "minimal"}
    expected_lines = [
        f"document:{hashlib.sha256(b'%PDF-1.4 sample').hexdigest()}",
        "provider:gemini",
        "model:gemini-test",
        f"prompt:{PROMPT_VERSION}",
        f"schema:{schema_sha256()}",
        "param:media_resolution=default",
        "param:thinking_level=minimal",
    ]
    expected = hashlib.sha256("\n".join(expected_lines).encode()).hexdigest()
    assert recording_key(DOC, "gemini", "gemini-test", params) == expected
    assert recording_key(DOC, "gemini", "gemini-test", dict(reversed(params.items()))) == expected


def test_key_changes_with_any_component() -> None:
    base = recording_key(DOC, "gemini", "m", {"a": "1"})
    assert recording_key(TEXT_DOC, "gemini", "m", {"a": "1"}) != base
    assert recording_key(DOC, "openai", "m", {"a": "1"}) != base
    assert recording_key(DOC, "gemini", "m2", {"a": "1"}) != base
    assert recording_key(DOC, "gemini", "m", {"a": "2"}) != base
    assert recording_key(DOC, "gemini", "m", {}) != base


def test_record_writes_file_without_secrets(tmp_path: Path, run_async: Any) -> None:
    inner = FakeProvider(raw_text='{"total": 1}')
    clock = lambda: datetime(2026, 10, 24, 12, 0, tzinfo=UTC)  # noqa: E731
    wrapper = RecordingProvider(inner, mode="record", root=tmp_path, clock=clock)
    response = run_async(wrapper.extract(DOC))
    path = tmp_path / "gemini" / "gemini-test" / f"{wrapper.key(DOC)}.json"
    assert path.is_file() and response.raw_text == '{"total": 1}'
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "format": 1,
        "provider": "gemini",
        "model": "gemini-test",
        "prompt_version": PROMPT_VERSION,
        "schema_sha256": schema_sha256(),
        "params": inner.params,
        "document_sha256": DOC.content_sha256(),
        "input": "application/pdf",
        "recorded_at": "2026-10-24T12:00:00+00:00",
        "usage": {
            "input_tokens": 120,
            "output_tokens": 40,
            "thinking_tokens": 10,
            "latency_ms": 321,
        },
        "raw_text": '{"total": 1}',
    }
    text = path.read_text(encoding="utf-8")
    assert "sample" not in text and "x-goog-api-key" not in text and "authorization" not in text


def test_replay_returns_recorded_answer_without_calling_inner(
    tmp_path: Path, run_async: Any
) -> None:
    recorder = RecordingProvider(FakeProvider('{"total": 2}'), mode="record", root=tmp_path)
    run_async(recorder.extract(DOC))
    inner = FakeProvider(fail=AssertionError("must not be called"))
    replayer = RecordingProvider(inner, mode="replay", root=tmp_path)
    response = run_async(replayer.extract(DOC))
    assert response.raw_text == '{"total": 2}'
    assert response.usage == UsageRecord("gemini", "gemini-test", "ok", 120, 40, 10, 321)
    assert response.attempts == (response.usage,) and inner.calls == 0


def test_replay_without_recording_never_touches_the_network(
    tmp_path: Path, mock_client: Any, fake_api_key: str, run_async: Any
) -> None:
    def exploding(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network request attempted in replay mode")

    provider = GeminiProvider(
        api_key=fake_api_key, model="gemini-3.5-flash-lite", client=mock_client(exploding)
    )
    wrapper = RecordingProvider(provider, mode="replay", root=tmp_path)
    with pytest.raises(RecordingMissingError, match="gemini/gemini-3.5-flash-lite"):
        run_async(wrapper.extract(DOC))
    assert not list(tmp_path.rglob("*.json"))


def test_record_skips_failed_calls(tmp_path: Path, run_async: Any) -> None:
    inner = FakeProvider(fail=InvalidResponseError("gemini: empty answer"))
    wrapper = RecordingProvider(inner, mode="record", root=tmp_path)
    with pytest.raises(InvalidResponseError):
        run_async(wrapper.extract(DOC))
    assert not list(tmp_path.rglob("*.json"))


def test_off_mode_passes_through(tmp_path: Path, run_async: Any) -> None:
    inner = FakeProvider('{"total": 3}')
    wrapper = RecordingProvider(inner, mode="off", root=tmp_path)
    assert run_async(wrapper.extract(TEXT_DOC)).raw_text == '{"total": 3}'
    assert inner.calls == 1 and not list(tmp_path.rglob("*.json"))
    assert wrapper.name == "gemini" and wrapper.model == "gemini-test"
    assert wrapper.params == inner.params
    with pytest.raises(ValueError):
        RecordingProvider(inner, mode="cache", root=tmp_path)  # type: ignore[arg-type]
