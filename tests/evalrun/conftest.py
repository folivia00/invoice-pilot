"""Fixtures for the evaluation runner tests: fake recordings built from the ground truth."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from helpers_eval import RECORDED_AT, answer_json

from evalrun.configs import CONFIGS
from evalrun.runner import DATASET_DIR, Dataset, document_inputs, load_dataset, replay_inner
from invoice_pilot.llm.recording import RecordingProvider
from invoice_pilot.llm.types import DocumentInput, ProviderResponse, UsageRecord


@pytest.fixture(scope="session")
def dataset() -> Dataset:
    return load_dataset(DATASET_DIR)


@pytest.fixture(scope="session")
def inputs(dataset: Dataset) -> dict[str, DocumentInput]:
    return document_inputs(dataset, DATASET_DIR)


@pytest.fixture(scope="session")
def truths(dataset: Dataset) -> dict[str, dict[str, Any]]:
    return {gt["id"]: gt for gt in dataset.ground_truths}


@pytest.fixture
def write_recordings(inputs: dict[str, DocumentInput]) -> Callable[..., RecordingProvider]:
    """Write fake recordings for ``config_name`` under ``root``; returns the replay wrapper."""

    def write(
        root: Path,
        config_name: str,
        answers: dict[str, str],
        *,
        input_tokens: int = 1000,
        output_tokens: int = 300,
        recorded_at: str = RECORDED_AT,
    ) -> RecordingProvider:
        config = CONFIGS[config_name]
        wrapper = RecordingProvider(replay_inner(config.llm), mode="replay", root=root)
        for doc_id, raw_text in answers.items():
            path = wrapper.path(inputs[doc_id])
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "format": 1,
                "provider": wrapper.name,
                "model": wrapper.model,
                "prompt_version": "extraction-v1",
                "schema_sha256": "test",
                "params": dict(wrapper.params),
                "document_sha256": inputs[doc_id].content_sha256(),
                "input": "text" if inputs[doc_id].is_text else inputs[doc_id].mime,
                "recorded_at": recorded_at,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "thinking_tokens": 0,
                    "latency_ms": 500,
                },
                "raw_text": raw_text,
            }
            path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return wrapper

    return write


class FakeLiveProvider:
    """Stands in for a real provider in record-mode tests (answers by document digest)."""

    def __init__(
        self,
        name: str,
        model: str,
        answers: dict[str, str],
        *,
        output_tokens: int = 300,
        api_key: str = "",
    ) -> None:
        self.name = name
        self.model = model
        self.params = {"fake": "1"}
        self.answers = answers
        self.output_tokens = output_tokens
        self.api_key = api_key
        self.calls = 0

    async def extract(self, doc: DocumentInput) -> ProviderResponse:
        self.calls += 1
        usage = UsageRecord(self.name, self.model, "ok", 1000, self.output_tokens, 0, 10, 200)
        return ProviderResponse(
            raw_text=self.answers[doc.content_sha256()], usage=usage, attempts=(usage,)
        )


@pytest.fixture
def fake_factory(
    inputs: dict[str, DocumentInput], truths: dict[str, dict[str, Any]]
) -> Callable[..., Any]:
    """Build a provider factory whose providers answer with the ground truth."""

    def build(output_tokens: int = 300) -> Any:
        created: list[FakeLiveProvider] = []
        by_digest = {
            inputs[doc_id].content_sha256(): answer_json(gt["invoice"])
            for doc_id, gt in truths.items()
        }

        def factory(config: Any, *, api_key: str, client: Any) -> FakeLiveProvider:
            provider = FakeLiveProvider(
                config.provider,
                config.model,
                by_digest,
                output_tokens=output_tokens,
                api_key=api_key,
            )
            # same parameters as the real provider, so replay finds the recordings
            provider.params = dict(replay_inner(config).params)
            created.append(provider)
            return provider

        factory.created = created  # type: ignore[attr-defined]
        return factory

    return build
