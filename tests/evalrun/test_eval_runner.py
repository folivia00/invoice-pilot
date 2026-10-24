"""Runner modes: replay gaps, record guards, cost cap, key secrecy, determinism, selection."""

import asyncio
import json
import logging
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from helpers_eval import answer_json

from evalrun.runner import (
    EXIT_INCOMPLETE,
    EXIT_OK,
    EXIT_REFUSED,
    Dataset,
    RunOptions,
    run_eval,
    select_documents,
)

CONFIG = "gemini-flash-lite"
THREE = ["inv-001", "inv-003", "inv-005"]  # text_pdf, scan_pdf, photo
SECRET = "sk-test-SECRET-VALUE-9876543210"


def _options(tmp_path: Path, **kwargs: Any) -> RunOptions:
    defaults: dict[str, Any] = {
        "configs": [CONFIG],
        "recordings_dir": tmp_path / "rec",
        "results_dir": tmp_path / "res",
    }
    return RunOptions(**{**defaults, **kwargs})


def _env_file(tmp_path: Path, key: str = "GEMINI_API_KEY") -> Path:
    path = tmp_path / "secrets.env"
    path.write_text(f"# test keys\n{key}={SECRET}\n", encoding="utf-8")
    return path


# --- replay ------------------------------------------------------------------------------


def test_replay_reports_missing_recordings(
    tmp_path: Path, truths: dict[str, dict[str, Any]], write_recordings: Callable[..., Any]
) -> None:
    write_recordings(
        tmp_path / "rec", CONFIG, {i: answer_json(truths[i]["invoice"]) for i in THREE[:2]}
    )
    result = asyncio.run(run_eval(_options(tmp_path, ids=THREE)))
    assert result.exit_code == EXIT_INCOMPLETE
    assert result.configs[CONFIG].missing == ["inv-005"]
    assert any("missing recordings: inv-005" in m for m in result.messages)
    metrics = json.loads((tmp_path / "res" / CONFIG / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["documents_selected"] == 3 and metrics["documents_evaluated"] == 2
    assert metrics["missing_recordings"] == ["inv-005"]
    rows = [
        json.loads(line)
        for line in (tmp_path / "res" / CONFIG / "predictions.jsonl")
        .read_text("utf-8")
        .splitlines()
    ]
    assert [r["status"] for r in rows] == ["auto_approved", "auto_approved", "missing_recording"]
    assert "inv-005" in (tmp_path / "res" / CONFIG / "summary.md").read_text(encoding="utf-8")


def test_replay_without_any_recordings_is_refused(tmp_path: Path) -> None:
    result = asyncio.run(run_eval(_options(tmp_path, configs=[])))
    assert result.exit_code == EXIT_REFUSED
    assert "no recordings found" in result.messages[0]


def test_replay_picks_configs_that_have_recordings(
    tmp_path: Path, truths: dict[str, dict[str, Any]], write_recordings: Callable[..., Any]
) -> None:
    answers = {i: answer_json(truths[i]["invoice"]) for i in THREE}
    write_recordings(tmp_path / "rec", "gpt-luna-low", answers)
    write_recordings(tmp_path / "rec", "gemini-flash", answers)
    result = asyncio.run(run_eval(_options(tmp_path, configs=[], ids=THREE)))
    assert result.exit_code == EXIT_OK
    assert sorted(result.configs) == ["gemini-flash", "gpt-luna-low"]  # not gpt-luna-auto
    assert (tmp_path / "res" / "gpt-luna-low" / "metrics.json").is_file()
    assert not (tmp_path / "res" / "gpt-luna-auto").exists()


def test_two_replays_are_byte_identical(
    tmp_path: Path, truths: dict[str, dict[str, Any]], write_recordings: Callable[..., Any]
) -> None:
    write_recordings(
        tmp_path / "rec", CONFIG, {i: answer_json(truths[i]["invoice"]) for i in THREE}
    )
    asyncio.run(run_eval(_options(tmp_path, ids=THREE)))
    files = ["metrics.json", "summary.md", "predictions.jsonl"]
    first = {f: (tmp_path / "res" / CONFIG / f).read_bytes() for f in files}
    asyncio.run(run_eval(_options(tmp_path, ids=THREE)))
    second = {f: (tmp_path / "res" / CONFIG / f).read_bytes() for f in files}
    assert first == second
    assert b"\r\n" not in first["metrics.json"] and b"\r\n" not in first["summary.md"]


# --- record guards -------------------------------------------------------------------------


def test_record_without_live_is_refused(tmp_path: Path, fake_factory: Callable[..., Any]) -> None:
    factory = fake_factory()
    options = _options(
        tmp_path, mode="record", env_file=_env_file(tmp_path), max_cost=Decimal("1"), ids=THREE
    )
    result = asyncio.run(run_eval(options, provider_factory=factory))
    assert result.exit_code == EXIT_REFUSED and "--live" in result.messages[0]
    assert factory.created == [] and not (tmp_path / "res").exists()


def test_record_needs_env_file_and_max_cost(
    tmp_path: Path, fake_factory: Callable[..., Any]
) -> None:
    factory = fake_factory()
    result = asyncio.run(
        run_eval(
            _options(tmp_path, mode="record", live=True, max_cost=Decimal("1")),
            provider_factory=factory,
        )
    )
    assert result.exit_code == EXIT_REFUSED and "--env-file" in result.messages[0]
    options = _options(tmp_path, mode="record", live=True, env_file=_env_file(tmp_path))
    result = asyncio.run(run_eval(options, provider_factory=factory))
    assert result.exit_code == EXIT_REFUSED and "--max-cost" in result.messages[0]
    assert factory.created == []


def test_record_with_missing_key_names_the_variable_only(
    tmp_path: Path, fake_factory: Callable[..., Any]
) -> None:
    options = _options(
        tmp_path,
        mode="record",
        live=True,
        env_file=_env_file(tmp_path, key="OPENAI_API_KEY"),
        max_cost=Decimal("1"),
        ids=THREE,
    )
    result = asyncio.run(run_eval(options, provider_factory=fake_factory()))
    assert result.exit_code == EXIT_REFUSED
    assert "GEMINI_API_KEY is missing" in result.messages[0]
    assert SECRET not in " ".join(result.messages)


def test_estimate_above_max_cost_is_refused_before_any_call(
    tmp_path: Path, fake_factory: Callable[..., Any]
) -> None:
    factory = fake_factory()
    options = _options(
        tmp_path,
        mode="record",
        live=True,
        env_file=_env_file(tmp_path),
        max_cost=Decimal("0.0001"),
        limit=5,
    )
    result = asyncio.run(run_eval(options, provider_factory=factory))
    assert result.exit_code == EXIT_REFUSED
    assert (
        "worst-case estimate" in result.messages[0] and "exceeds --max-cost" in result.messages[0]
    )
    assert factory.created == [] and not (tmp_path / "rec").exists()


def test_actual_cost_above_cap_stops_after_current_document(
    tmp_path: Path, fake_factory: Callable[..., Any]
) -> None:
    # each fake answer costs 200_000 x 2.50 / 1M = 0.50 USD; the cap allows one, not two
    factory = fake_factory(output_tokens=200_000)
    options = _options(
        tmp_path,
        mode="record",
        live=True,
        env_file=_env_file(tmp_path),
        max_cost=Decimal("0.60"),
        ids=THREE,
        concurrency=1,
    )
    result = asyncio.run(run_eval(options, provider_factory=factory))
    config = result.configs[CONFIG]
    assert result.exit_code == EXIT_INCOMPLETE and config.stopped == "max_cost"
    assert config.processed == 2 and config.selected == 3
    # two answers: 2 x (1000 x 0.30 + 200_000 x 2.50) / 1M
    assert config.cost_usd == Decimal("1.0006") and factory.created[0].calls == 2
    assert any("stopped after 2 documents" in m for m in result.messages)
    rows = (tmp_path / "res" / CONFIG / "predictions.jsonl").read_text("utf-8").splitlines()
    assert len(rows) == 2
    metrics = json.loads((tmp_path / "res" / CONFIG / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["run"]["stopped"] == "max_cost" and metrics["run"]["documents_processed"] == 2
    assert len(list((tmp_path / "rec").rglob("*.json"))) == 2


def test_record_writes_recordings_and_replay_reads_them(
    tmp_path: Path, fake_factory: Callable[..., Any]
) -> None:
    factory = fake_factory()
    options = _options(
        tmp_path,
        mode="record",
        live=True,
        env_file=_env_file(tmp_path),
        max_cost=Decimal("1"),
        ids=THREE,
    )
    result = asyncio.run(run_eval(options, provider_factory=factory))
    assert result.exit_code == EXIT_OK and factory.created[0].api_key == SECRET
    assert len(list((tmp_path / "rec").rglob("*.json"))) == 3
    replay = asyncio.run(run_eval(_options(tmp_path, ids=THREE)))
    assert replay.exit_code == EXIT_OK
    metrics = json.loads((tmp_path / "res" / CONFIG / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["overall"]["precision_auto_approved"]["value"] == 1.0
    assert metrics["run"]["mode"] == "replay" and metrics["run"]["run_date"] is not None


def test_key_value_never_reaches_output_or_logs(
    tmp_path: Path,
    fake_factory: Callable[..., Any],
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    options = _options(
        tmp_path,
        mode="record",
        live=True,
        env_file=_env_file(tmp_path),
        max_cost=Decimal("1"),
        ids=THREE,
    )
    with caplog.at_level(logging.DEBUG):
        result = asyncio.run(run_eval(options, provider_factory=fake_factory()))
    for line in result.messages:
        print(line)
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err + caplog.text
    written = "".join(p.read_text("utf-8") for p in (tmp_path / "res").rglob("*") if p.is_file())
    written += "".join(p.read_text("utf-8") for p in (tmp_path / "rec").rglob("*.json"))
    assert SECRET not in written


# --- selection -----------------------------------------------------------------------------


def test_limit_is_balanced_over_input_types(dataset: Dataset) -> None:
    chosen = select_documents(dataset, None, 5)
    assert [gt["id"] for gt in chosen] == ["inv-001", "inv-002", "inv-003", "inv-004", "inv-005"]
    assert sorted(gt["input_type"] for gt in chosen) == [
        "photo",
        "scan_pdf",
        "scan_pdf",
        "text_pdf",
        "text_pdf",
    ]
    ten = select_documents(dataset, None, 10)
    assert {gt["input_type"] for gt in ten} == {"text_pdf", "scan_pdf", "photo"}
    assert len(select_documents(dataset, None, 500)) == 120
    assert [gt["id"] for gt in select_documents(dataset, ["inv-050", "inv-002"], None)] == [
        "inv-002",
        "inv-050",
    ]


def test_unknown_ids_are_refused(tmp_path: Path) -> None:
    result = asyncio.run(run_eval(_options(tmp_path, ids=["inv-999"])))
    assert result.exit_code == EXIT_REFUSED and "inv-999" in result.messages[0]
