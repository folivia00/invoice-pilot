"""Runner: stop on a provider spend limit; --skip-recorded; single prepare() per document."""

import asyncio
import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from helpers_eval import answer_json

from evalrun.configs import CONFIGS
from evalrun.cost import estimate_cost
from evalrun.runner import EXIT_INCOMPLETE, EXIT_OK, STOP_SPEND_LIMIT, RunOptions, run_eval
from invoice_pilot.llm.providers.gemini import GeminiProvider

CONFIG = "gemini-flash-lite"
THREE = ["inv-001", "inv-003", "inv-005"]
SECRET = "sk-test-SECRET-VALUE-9876543210"


def _options(tmp_path: Path, **kwargs: Any) -> RunOptions:
    defaults: dict[str, Any] = {
        "configs": [CONFIG],
        "recordings_dir": tmp_path / "rec",
        "results_dir": tmp_path / "res",
    }
    return RunOptions(**{**defaults, **kwargs})


def _env_file(tmp_path: Path) -> Path:
    path = tmp_path / "llm.env"
    path.write_text(f"GEMINI_API_KEY={SECRET}\n", encoding="utf-8")
    return path


def test_spend_limit_stops_the_run_immediately(
    tmp_path: Path, fake_sleep: Any, truths: dict[str, dict[str, Any]]
) -> None:
    calls: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = {"error": {"code": "project_spend_limit_exceeded", "message": "limit", "type": "x"}}
        return httpx.Response(429, json=body)

    def factory(config: Any, *, api_key: str, client: Any) -> GeminiProvider:
        return GeminiProvider(
            api_key=api_key,
            model=config.model,
            client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
            sleep=fake_sleep,
            jitter=lambda: 0.0,
        )

    options = _options(
        tmp_path,
        mode="record",
        live=True,
        env_file=_env_file(tmp_path),
        max_cost=Decimal("1"),
        ids=THREE,
        concurrency=1,
    )
    result = asyncio.run(run_eval(options, provider_factory=factory))
    config = result.configs[CONFIG]
    assert result.exit_code == EXIT_INCOMPLETE
    assert (
        config.stopped == STOP_SPEND_LIMIT and config.stop_detail == "project_spend_limit_exceeded"
    )
    assert len(calls) == 1 and config.processed == 1 and config.selected == 3
    assert any("spend limit reached (project_spend_limit_exceeded)" in m for m in result.messages)
    rows = [
        json.loads(line)
        for line in (tmp_path / "res" / CONFIG / "predictions.jsonl")
        .read_text("utf-8")
        .splitlines()
    ]
    assert len(rows) == 1 and rows[0]["status"] == "provider_error"
    assert rows[0]["spend_limit_code"] == "project_spend_limit_exceeded"
    metrics = json.loads((tmp_path / "res" / CONFIG / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["run"]["stopped"] == "provider_spend_limit" and metrics["provider_errors"] == 1
    assert not list((tmp_path / "rec").rglob("*.json"))


def test_skip_recorded_requests_only_new_documents(
    tmp_path: Path,
    truths: dict[str, dict[str, Any]],
    write_recordings: Callable[..., Any],
    fake_factory: Callable[..., Any],
) -> None:
    write_recordings(
        tmp_path / "rec", CONFIG, {i: answer_json(truths[i]["invoice"]) for i in THREE[:2]}
    )
    factory = fake_factory()
    options = _options(
        tmp_path,
        mode="record",
        live=True,
        env_file=_env_file(tmp_path),
        max_cost=Decimal("1"),
        ids=THREE,
        skip_recorded=True,
    )
    result = asyncio.run(run_eval(options, provider_factory=factory))
    config = result.configs[CONFIG]
    assert result.exit_code == EXIT_OK
    assert factory.created[0].calls == 1 and config.skipped == 2 and config.processed == 1
    expected = estimate_cost(CONFIGS[CONFIG], ["photo"], date.today())
    assert any(
        f"${expected:.4f} for 1 documents (2 already recorded, skipped)" in m
        for m in result.messages
    )
    rows = {
        row["id"]: row
        for row in map(
            json.loads,
            (tmp_path / "res" / CONFIG / "predictions.jsonl").read_text("utf-8").splitlines(),
        )
    }
    assert (
        rows["inv-001"]["status"] == "skipped_recorded"
        and rows["inv-003"]["status"] == "skipped_recorded"
    )
    assert rows["inv-005"]["status"] == "auto_approved"
    metrics = json.loads((tmp_path / "res" / CONFIG / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["skipped_recorded"] == 2 and metrics["documents_evaluated"] == 1
    assert len(list((tmp_path / "rec").rglob("*.json"))) == 3
    # a later replay evaluates all three
    replay = asyncio.run(run_eval(_options(tmp_path, ids=THREE)))
    assert (
        replay.exit_code == EXIT_OK and replay.configs[CONFIG].metrics["documents_evaluated"] == 3
    )


def test_skip_recorded_is_ignored_in_replay(
    tmp_path: Path, truths: dict[str, dict[str, Any]], write_recordings: Callable[..., Any]
) -> None:
    write_recordings(
        tmp_path / "rec", CONFIG, {i: answer_json(truths[i]["invoice"]) for i in THREE}
    )
    result = asyncio.run(run_eval(_options(tmp_path, ids=THREE, skip_recorded=True)))
    assert result.exit_code == EXIT_OK and result.configs[CONFIG].skipped == 0


def test_runner_prepares_each_document_once(
    tmp_path: Path,
    truths: dict[str, dict[str, Any]],
    write_recordings: Callable[..., Any],
    monkeypatch: Any,
) -> None:
    import evalrun.runner as runner_module
    import invoice_pilot.services.extraction as service_module

    write_recordings(
        tmp_path / "rec", CONFIG, {i: answer_json(truths[i]["invoice"]) for i in THREE}
    )
    counts = {"runner": 0, "service": 0}
    real_prepare = runner_module.prepare

    def counting_runner_prepare(data: bytes, **kwargs: Any) -> Any:
        counts["runner"] += 1
        return real_prepare(data, **kwargs)

    def counting_service_prepare(data: bytes, **kwargs: Any) -> Any:
        counts["service"] += 1
        return real_prepare(data, **kwargs)

    monkeypatch.setattr(runner_module, "prepare", counting_runner_prepare)
    monkeypatch.setattr(service_module, "prepare", counting_service_prepare)
    result = asyncio.run(run_eval(_options(tmp_path, ids=THREE)))
    assert result.exit_code == EXIT_OK
    assert counts == {"runner": 3, "service": 0}
