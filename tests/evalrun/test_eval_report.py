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
    README_END,
    README_START,
    extract_readme_block,
    readme_block_matches,
    render_readme_block,
    replace_readme_block,
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
    block = render_readme_block([_metrics(tmp_path, truths, write_recordings)])
    assert block.startswith(README_START) and block.endswith(README_END)
    assert "| gemini-flash-lite | gemini-3.5-flash-lite | 3 | 100.0 % (3/3) |" in block
    assert "$1.05" in block
    readme = tmp_path / "README.md"
    readme.write_text(f"# Title\n\nIntro.\n\n{block}\n\nOutro.\n", encoding="utf-8")
    assert readme_block_matches(readme, block)
    stale = block.replace("100.0 % (3/3)", "99.0 % (3/3)")
    readme.write_text(f"# Title\n\n{stale}\n", encoding="utf-8")
    assert not readme_block_matches(readme, block)
    readme.write_text("# Title without markers\n", encoding="utf-8")
    assert not readme_block_matches(readme, block)
    assert extract_readme_block("no markers") is None


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
