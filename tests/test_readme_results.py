"""The README Results section matches eval/results (CI replays the eval before this test)."""

import json
import math
import statistics
from pathlib import Path
from typing import Any

from evalrun.report import pct, readme_block_matches, render_readme_block

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "eval" / "results"
README = REPO / "README.md"


def _metrics() -> list[dict[str, Any]]:
    paths = sorted(RESULTS.glob("*/metrics.json"))
    assert paths, "no eval results"
    return [json.loads(path.read_text(encoding="utf-8")) for path in paths]


def _primary() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    folder = RESULTS / "gemini-flash-lite"
    metrics = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))
    lines = (folder / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
    return metrics, [json.loads(line) for line in lines]


def test_readme_block_matches_the_generated_one() -> None:
    assert readme_block_matches(README, render_readme_block(_metrics()))


def test_readme_numbers_next_to_the_block_match_the_results() -> None:
    metrics, predictions = _primary()
    overall = metrics["overall"]
    readme = README.read_text(encoding="utf-8")
    expected_code = pct(overall["corrupted_recall_expected_code"])
    clean_review = pct(overall["clean_sent_to_review"])
    assert f"expected issue code: {expected_code}." in readme
    assert f"Clean invoices sent to review: {clean_review}." in readme
    assert f"Run of {metrics['run']['run_date']} on the Gemini API free tier." in readme

    latencies = sorted(
        usage["latency_ms"]
        for row in predictions
        for usage in row["usage"]
        if usage["status"] == "ok"
    )
    median_s = statistics.median(latencies) / 1000
    p95_s = latencies[math.ceil(0.95 * len(latencies)) - 1] / 1000  # nearest rank
    assert (
        f"median {median_s:.1f} s, p95 {p95_s:.1f} s ({len(latencies)} recorded calls"
    ) in readme


def test_readme_failures_are_the_wrong_auto_approved_documents() -> None:
    _, predictions = _primary()
    wrong = sorted(
        row["id"]
        for row in predictions
        if row["status"] == "auto_approved" and not row["key_fields_correct"]
    )
    approved = sum(row["status"] == "auto_approved" for row in predictions)
    readme = README.read_text(encoding="utf-8")
    assert f"{len(wrong)} of the {approved} auto-approved invoices" in readme
    for doc_id in wrong:
        assert f"`{doc_id}`" in readme
