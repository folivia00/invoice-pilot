"""README block, cost report, cost estimate and env-file parsing."""

import asyncio
import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from helpers_eval import answer_json

from evalrun.configs import CONFIGS, get_config
from evalrun.cost import TOKEN_UPPER_BOUNDS, bounds_key, estimate_cost
from evalrun.envfile import EnvFileError, parse_env_file, require_key
from evalrun.report import (
    CI_NOTE,
    README_END,
    README_START,
    ci_text,
    extract_readme_block,
    latency_stats,
    load_runs,
    pct_ci,
    readme_block_matches,
    render_readme_block,
    replace_readme_block,
    wilson_interval,
)
from evalrun.runner import RunOptions, run_eval

CONFIG = "gemini-flash-lite"
THREE = ["inv-001", "inv-003", "inv-005"]


def _metrics(tmp_path: Path, truths: dict[str, Any], write_recordings: Callable[..., Any]) -> dict:
    write_recordings(
        tmp_path / "rec", CONFIG, {i: answer_json(truths[i]["invoice"]) for i in THREE}
    )
    options = RunOptions(
        configs=[CONFIG], ids=THREE, recordings_dir=tmp_path / "rec", results_dir=tmp_path / "res"
    )
    assert asyncio.run(run_eval(options)).exit_code == 0
    return json.loads((tmp_path / "res" / CONFIG / "metrics.json").read_text(encoding="utf-8"))


def test_readme_block_matches_and_mismatches(
    tmp_path: Path, truths: dict[str, Any], write_recordings: Callable[..., Any]
) -> None:
    _metrics(tmp_path, truths, write_recordings)
    block = render_readme_block(load_runs(tmp_path / "res"))
    assert block.startswith(README_START) and block.endswith(README_END)
    assert (
        "| gemini-flash-lite | gemini-3.5-flash-lite | 3 | 100.0 % (3/3; 95 % CI 43.8–100.0) |"
    ) in block
    assert "$1.05" in block
    assert "- Run of " in block and "paid-tier list prices." in block
    assert "**By input type** (gemini-flash-lite):" in block
    assert "| Text PDF | 1 | 1/1 | 100.0 % (1/1; 95 % CI 20.7–100.0) |" in block
    assert "| n/a (0/0) |\n" in block  # no corrupted documents in this slice
    assert block.endswith(f"{CI_NOTE}\n{README_END}")
    readme = tmp_path / "README.md"
    readme.write_text(f"# Title\n\nIntro.\n\n{block}\n\nOutro.\n", encoding="utf-8")
    assert readme_block_matches(readme, block)
    stale = block.replace("100.0 % (3/3)", "99.0 % (3/3)")
    readme.write_text(f"# Title\n\n{stale}\n", encoding="utf-8")
    assert not readme_block_matches(readme, block)
    readme.write_text("# Title without markers\n", encoding="utf-8")
    assert not readme_block_matches(readme, block)
    assert extract_readme_block("no markers") is None


def test_wilson_interval_reference_values() -> None:
    # Reference bounds recomputed by inverting the score test numerically (report 18).
    assert ci_text(101, 104) == "91.9–99.0"
    low, high = wilson_interval(101, 104) or (0.0, 0.0)
    assert round(low * 100, 1) == 91.9 and round(high * 100, 1) == 99.0
    assert ci_text(15, 15).endswith("–100.0")
    assert wilson_interval(15, 15) == (pytest.approx(0.79611, abs=1e-5), 1.0)
    assert ci_text(0, 10).startswith("0.0–")
    assert wilson_interval(0, 10) == (0.0, pytest.approx(0.27754, abs=1e-5))
    assert wilson_interval(0, 0) is None
    assert ci_text(0, 0) == "n/a"


def test_pct_ci_formats_the_main_metric() -> None:
    item = {"value": 0.9712, "numerator": 101, "denominator": 104}
    assert pct_ci(item) == "97.1 % (101/104; 95 % CI 91.9–99.0)"
    assert pct_ci({"value": None, "numerator": 0, "denominator": 0}) == "n/a (0/0)"


def test_latency_stats_skips_failed_calls() -> None:
    def call(ms: int, status: str = "ok") -> dict[str, Any]:
        return {"latency_ms": ms, "status": status}

    rows = [
        {"usage": [call(900, "error"), call(1000)]},
        {"usage": [call(3000)]},
        {"usage": [call(2000)]},
    ]
    assert latency_stats(rows) == (2000, 3000, 3)
    assert latency_stats([{"usage": [call(5, "error")]}]) is None


def test_replace_readme_block() -> None:
    text = f"a\n{README_START}\nold\n{README_END}\nb\n"
    assert replace_readme_block(text, f"{README_START}\nnew\n{README_END}") == (
        f"a\n{README_START}\nnew\n{README_END}\nb\n"
    )
    with pytest.raises(ValueError):
        replace_readme_block("no markers", "x")


def test_cost_report_from_recordings(
    tmp_path: Path, truths: dict[str, Any], write_recordings: Callable[..., Any]
) -> None:
    from cost_report import build_report

    write_recordings(
        tmp_path / "rec", CONFIG, {i: answer_json(truths[i]["invoice"]) for i in THREE}
    )
    write_recordings(
        tmp_path / "rec", "gpt-luna-low", {i: answer_json(truths[i]["invoice"]) for i in THREE[:2]}
    )
    report = build_report([], recordings_dir=tmp_path / "rec")
    assert "## gemini-flash-lite (gemini-3.5-flash-lite)" in report
    assert "## gpt-luna-low (gpt-6-luna)" in report and "gpt-luna-auto" not in report
    assert "**1,000 invoices ~ $1.05 on LLM calls**" in report
    # gpt-6-luna: 1000 x 0.10 + 300 x 0.50 per million = 0.00025 per document
    assert "| gpt-luna-low | gpt-6-luna | 2 | 1000.0 | 300.0 | $0.000250 | $0.25 |" in report
    assert "| gemini-flash-lite | gemini-3.5-flash-lite | 3 |" in report
    assert "2026-10-25 .. 2026-10-25" in report
    only = build_report([CONFIG], recordings_dir=tmp_path / "rec")
    assert "gpt-luna-low" not in only


def test_estimate_uses_upper_bounds_and_prices() -> None:
    day = date(2026, 10, 24)
    kinds = ["text_pdf", "scan_pdf", "photo"]
    gemini = estimate_cost(get_config("gemini-flash-lite"), kinds, day)
    # (1700 + 2500 + 2500) x 0.30 + 3 x 800 x 2.50 per million
    assert gemini == Decimal("0.00201") + Decimal("0.006")
    assert estimate_cost(get_config("gemini-flash"), kinds, day) > gemini
    assert estimate_cost(get_config("gemini-flash"), kinds, date(2027, 1, 1)) == 2 * estimate_cost(
        get_config("gemini-flash"), kinds, day
    )
    low = estimate_cost(get_config("gpt-luna-low"), ["photo"], day)
    auto = estimate_cost(get_config("gpt-luna-auto"), ["photo"], day)
    assert low < auto
    assert estimate_cost(get_config("gemini-flash-lite"), [], day) == 0
    assert {bounds_key(c) for c in CONFIGS.values()} == set(TOKEN_UPPER_BOUNDS)
    for bounds in TOKEN_UPPER_BOUNDS.values():
        assert set(bounds) == set(kinds)


def test_env_file_parsing(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n\nGEMINI_API_KEY=abc123\nexport OPENAI_API_KEY='quoted value'\n"
        'OTHER="x=y"\nEMPTY=\n',
        encoding="utf-8",
    )
    values = parse_env_file(path)
    assert values == {
        "GEMINI_API_KEY": "abc123",
        "OPENAI_API_KEY": "quoted value",
        "OTHER": "x=y",
        "EMPTY": "",
    }
    assert require_key(values, "GEMINI_API_KEY", path) == "abc123"
    with pytest.raises(EnvFileError, match="EMPTY is missing or empty"):
        require_key(values, "EMPTY", path)
    with pytest.raises(EnvFileError, match="MISSING is missing"):
        require_key(values, "MISSING", path)
    path.write_text("no equals sign\n", encoding="utf-8")
    with pytest.raises(EnvFileError, match="expected KEY=VALUE"):
        parse_env_file(path)
    with pytest.raises(EnvFileError, match="not found"):
        parse_env_file(tmp_path / "absent.env")
