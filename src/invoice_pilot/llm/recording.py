"""Record / replay wrapper around a provider (eval runs without the network).

Key = sha256 over the document digest, provider, model, prompt version, schema
digest and the provider parameters that change the answer. A recording stores
the raw model text and token usage only: no API key, no request headers, no
document content.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

from invoice_pilot.llm.errors import RecordingMissingError
from invoice_pilot.llm.prompts import PROMPT_VERSION
from invoice_pilot.llm.providers import Provider
from invoice_pilot.llm.schema import schema_sha256
from invoice_pilot.llm.types import DocumentInput, ProviderResponse, UsageRecord

RecordingMode = Literal["off", "record", "replay"]
RECORDING_FORMAT: Final = 1


def recording_key(doc: DocumentInput, provider: str, model: str, params: Mapping[str, str]) -> str:
    """Stable key for (document, provider, model, prompt version, schema, parameters)."""
    lines = [
        f"document:{doc.content_sha256()}",
        f"provider:{provider}",
        f"model:{model}",
        f"prompt:{PROMPT_VERSION}",
        f"schema:{schema_sha256()}",
        *(f"param:{key}={params[key]}" for key in sorted(params)),
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


class RecordingProvider:
    """Delegates to ``inner``; ``record`` saves successful answers, ``replay`` serves them."""

    def __init__(
        self,
        inner: Provider,
        *,
        mode: RecordingMode,
        root: Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if mode not in ("off", "record", "replay"):
            raise ValueError(f"unknown recording mode {mode!r}")
        self._inner = inner
        self.mode: RecordingMode = mode
        self.root = root
        self._clock = clock

    def __repr__(self) -> str:
        return f"RecordingProvider(mode={self.mode!r}, inner={self._inner!r})"

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def params(self) -> Mapping[str, str]:
        return self._inner.params

    def key(self, doc: DocumentInput) -> str:
        return recording_key(doc, self.name, self.model, self.params)

    def path(self, doc: DocumentInput) -> Path:
        return self.root / self.name / self.model / f"{self.key(doc)}.json"

    async def extract(self, doc: DocumentInput) -> ProviderResponse:
        if self.mode == "off":
            return await self._inner.extract(doc)
        path = self.path(doc)
        if self.mode == "replay":
            if not path.is_file():
                raise RecordingMissingError(
                    f"no recording for {self.name}/{self.model} key {path.stem[:12]}"
                )
            return self._load(path)
        response = await self._inner.extract(doc)  # failures propagate, nothing is written
        self._write(path, doc, response)
        return response

    def _write(self, path: Path, doc: DocumentInput, response: ProviderResponse) -> None:
        usage = response.usage
        payload: dict[str, Any] = {
            "format": RECORDING_FORMAT,
            "provider": self.name,
            "model": self.model,
            "prompt_version": PROMPT_VERSION,
            "schema_sha256": schema_sha256(),
            "params": dict(self.params),
            "document_sha256": doc.content_sha256(),
            "input": "text" if doc.is_text else doc.mime,
            "recorded_at": self._clock().isoformat(timespec="seconds"),
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "thinking_tokens": usage.thinking_tokens,
                "latency_ms": usage.latency_ms,
            },
            "raw_text": response.raw_text,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def _load(self, path: Path) -> ProviderResponse:
        payload = json.loads(path.read_text(encoding="utf-8"))
        usage_data = payload["usage"]
        usage = UsageRecord(
            provider=self.name,
            model=self.model,
            status="ok",
            input_tokens=int(usage_data["input_tokens"]),
            output_tokens=int(usage_data["output_tokens"]),
            thinking_tokens=int(usage_data.get("thinking_tokens", 0)),
            latency_ms=int(usage_data.get("latency_ms", 0)),
        )
        return ProviderResponse(raw_text=payload["raw_text"], usage=usage, attempts=(usage,))
