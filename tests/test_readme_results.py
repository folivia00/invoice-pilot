"""The README Results section matches eval/results (CI replays the eval before this test)."""

import json
from collections import Counter
from pathlib import Path
from typing import Any

from evalrun.metrics import INPUT_TYPES
from evalrun.report import README_END, extract_readme_block, load_runs, render_readme_block

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "eval" / "results"
GROUND_TRUTH = REPO / "eval" / "dataset" / "ground_truth"
README = REPO / "README.md"

# Counters that must add up over the input types to the overall value.
SUMMED = (
    "all_key_fields_correct",
    "auto_approved",
    "needs_review",
    "precision_auto_approved",
    "corrupted_recall",
    "corrupted_recall_expected_code",
    "clean_sent_to_review",
)


def _primary() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    runs = {metrics["run"]["config"]: (metrics, preds) for metrics, preds in load_runs(RESULTS)}
    return runs["gemini-flash-lite"]


def test_readme_block_matches_the_generated_one() -> None:
    runs = load_runs(RESULTS)
    assert runs, "no eval results"
    assert extract_readme_block(README.read_text(encoding="utf-8")) == render_readme_block(runs)


def test_run_notes_live_only_inside_the_block() -> None:
    readme = README.read_text(encoding="utf-8")
    outside = readme.split(README_END, 1)[1]
    notes = ("expected issue code:", "Model call latency", "paid-tier list prices.", "; 95 % CI")
    for phrase in notes:
        assert phrase not in outside


def test_input_types_add_up_to_the_totals() -> None:
    for metrics, _ in load_runs(RESULTS):
        groups = [metrics["by_input_type"][kind] for kind in INPUT_TYPES]
        assert set(metrics["by_input_type"]) == set(INPUT_TYPES)
        assert sum(g["documents"] for g in groups) == metrics["documents_evaluated"]
        for key in SUMMED:
            for part in ("numerator", "denominator"):
                assert sum(g[key][part] for g in groups) == metrics["overall"][key][part], key


def test_input_type_breakdown_matches_predictions_and_dataset() -> None:
    metrics, predictions = _primary()
    for row in predictions:
        truth = json.loads((GROUND_TRUTH / f"{row['id']}.json").read_text(encoding="utf-8"))
        assert row["input_type"] == truth["input_type"], row["id"]
    for kind in INPUT_TYPES:
        rows = [r for r in predictions if r["input_type"] == kind]
        auto = [r for r in rows if r["status"] == "auto_approved"]
        corrupted = [r for r in rows if r["corruption"]]
        group = metrics["by_input_type"][kind]
        assert group["documents"] == len(rows)
        assert group["auto_approved"]["numerator"] == len(auto)
        assert group["precision_auto_approved"]["numerator"] == sum(
            r["key_fields_correct"] for r in auto
        )
        assert group["all_key_fields_correct"]["numerator"] == sum(
            r["key_fields_correct"] for r in rows
        )
        assert group["corrupted_recall"]["numerator"] == sum(
            r["status"] == "needs_review" for r in corrupted
        )
        assert group["corrupted_recall"]["denominator"] == len(corrupted)


def test_readme_dataset_counts_match_the_results() -> None:
    metrics, predictions = _primary()
    docs = {kind: metrics["by_input_type"][kind]["documents"] for kind in INPUT_TYPES}
    languages = Counter(row["language"] for row in predictions)
    corrupted = metrics["overall"]["corrupted_recall"]["denominator"]
    total = metrics["documents_evaluated"]
    readme = README.read_text(encoding="utf-8")
    assert f"Measured on the synthetic dataset v1.0 ({total} invoices)." in readme
    assert (
        f"{total} synthetic invoices: {docs['text_pdf']} text PDFs, {docs['scan_pdf']} scans "
        f"and {docs['photo']} photos; {languages['en']} English and {languages['de']} German"
    ) in readme
    assert f"{corrupted} of them carry a deliberate error" in readme


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
