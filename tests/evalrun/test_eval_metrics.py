"""Metrics on fake recordings: a perfect model, a noisy model, a model that repairs totals."""

import asyncio
import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from helpers_eval import answer_json

from evalrun.metrics import percentile, ratio
from evalrun.runner import RunOptions, run_eval

CONFIG = "gemini-flash-lite"
TEN = [f"inv-{i:03d}" for i in range(1, 11)]


def _replay(tmp_path: Path, ids: list[str] | None = None) -> dict[str, Any]:
    options = RunOptions(
        configs=[CONFIG],
        ids=ids,
        recordings_dir=tmp_path / "rec",
        results_dir=tmp_path / "res",
    )
    result = asyncio.run(run_eval(options))
    assert result.exit_code == 0, result.messages
    return json.loads((tmp_path / "res" / CONFIG / "metrics.json").read_text(encoding="utf-8"))


def test_perfect_model_on_the_whole_dataset(
    tmp_path: Path, truths: dict[str, dict[str, Any]], write_recordings: Callable[..., Any]
) -> None:
    write_recordings(
        tmp_path / "rec", CONFIG, {i: answer_json(gt["invoice"]) for i, gt in truths.items()}
    )
    metrics = _replay(tmp_path)
    overall = metrics["overall"]
    assert metrics["documents_evaluated"] == 120 and metrics["missing_recordings"] == []
    assert all(f["value"] == 1.0 for f in metrics["field_accuracy"]["key"].values())
    assert all(f["value"] == 1.0 for f in metrics["field_accuracy"]["text"].values())
    assert overall["all_key_fields_correct"] == ratio(120, 120)
    assert overall["auto_approved"] == ratio(105, 120)
    assert overall["needs_review"] == ratio(15, 120)
    assert overall["precision_auto_approved"] == ratio(105, 105)
    assert overall["corrupted_recall"] == ratio(15, 15)
    assert overall["corrupted_recall_expected_code"] == ratio(15, 15)
    assert overall["clean_sent_to_review"] == ratio(0, 105)
    assert metrics["extraction_failed"] == 0 and metrics["provider_errors"] == 0
    assert metrics["by_input_type"]["text_pdf"]["documents"] == 48
    assert metrics["by_language"]["de"]["documents"] == 48
    assert metrics["model_only_vs_with_checks"] == {
        "model_only": ratio(120, 120),
        "model_with_checks": ratio(105, 105),
    }
    cost = metrics["cost"]["overall"]
    assert cost["documents"] == 120 and cost["input_tokens"] == {"mean": 1000.0, "p95": 1000}
    # 1000 x 0.30 + 300 x 2.50 per million = 0.00105 USD per document
    assert cost["cost_usd_per_document"] == "0.001050" and cost["cost_usd_per_1000"] == "1.05"
    assert metrics["run"]["run_date"] == "2026-10-25"


def _noisy_answers(truths: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Ten documents with deterministic damage (see expected numbers in the test)."""
    answers = {}
    for doc_id in TEN:
        invoice = json.loads(json.dumps(truths[doc_id]["invoice"]))
        if doc_id == "inv-001":  # clean: wrong total -> checks catch it (total_mismatch)
            invoice["total"] = str(Decimal(invoice["total"]) + Decimal("10"))
        elif doc_id == "inv-002":  # clean: IBAN dropped -> no check fails, field wrong
            assert invoice["iban"] is not None
            invoice["iban"] = None
        elif doc_id == "inv-003":  # clean: line items in the wrong order -> sums still add up
            assert len(invoice["line_items"]) > 1
            invoice["line_items"] = list(reversed(invoice["line_items"]))
        elif doc_id == "inv-007":  # corrupted (total_mismatch): model "repairs" the total
            assert truths[doc_id]["corruption"] == "total_mismatch"
            invoice["total"] = str(Decimal(invoice["subtotal"]) + Decimal(invoice["tax_amount"]))
        answers[doc_id] = answer_json(invoice)
    return answers


def test_noisy_model_matches_hand_computed_numbers(
    tmp_path: Path, truths: dict[str, dict[str, Any]], write_recordings: Callable[..., Any]
) -> None:
    assert [truths[i]["corruption"] for i in TEN].count(None) == 8  # inv-007 and inv-009 corrupted
    write_recordings(tmp_path / "rec", CONFIG, _noisy_answers(truths))
    metrics = _replay(tmp_path, TEN)
    overall = metrics["overall"]
    # wrong somewhere: 001 (total), 002 (iban), 003 (line order), 007 (repaired total)
    assert overall["all_key_fields_correct"] == ratio(6, 10)
    # needs_review: 001 (total_mismatch), 009 (printed line_amount_mismatch); 007 slips through
    assert overall["auto_approved"] == ratio(8, 10)
    assert overall["needs_review"] == ratio(2, 10)
    # auto-approved and fully right: 004, 005, 006, 008, 010
    assert overall["precision_auto_approved"] == ratio(5, 8)
    assert overall["corrupted_recall"] == ratio(1, 2)
    assert overall["corrupted_recall_expected_code"] == ratio(1, 2)
    assert overall["clean_sent_to_review"] == ratio(1, 8)
    fields = metrics["field_accuracy"]
    assert fields["key"]["total"] == ratio(8, 10)
    assert fields["key"]["iban"] == ratio(9, 10)
    assert fields["key"]["line_items"] == ratio(9, 10)
    assert fields["key"]["invoice_number"] == ratio(10, 10)
    assert fields["text"]["line_items.description"] == ratio(9, 10)
    by_type = metrics["by_input_type"]
    assert by_type["text_pdf"]["documents"] == 4 and by_type["scan_pdf"]["documents"] == 4
    assert by_type["photo"] == {
        **by_type["photo"],
        "documents": 2,
        "all_key_fields_correct": ratio(2, 2),
        "precision_auto_approved": ratio(2, 2),
    }
    assert metrics["by_language"]["en"]["documents"] == 5
    assert metrics["model_only_vs_with_checks"] == {
        "model_only": ratio(6, 10),
        "model_with_checks": ratio(5, 8),
    }


def test_repaired_total_counts_against_precision_and_recall(
    tmp_path: Path, truths: dict[str, dict[str, Any]], write_recordings: Callable[..., Any]
) -> None:
    gt = truths["inv-007"]
    repaired = json.loads(json.dumps(gt["invoice"]))
    repaired["total"] = str(Decimal(repaired["subtotal"]) + Decimal(repaired["tax_amount"]))
    assert repaired["total"] != gt["invoice"]["total"]
    write_recordings(tmp_path / "rec", CONFIG, {"inv-007": answer_json(repaired)})
    metrics = _replay(tmp_path, ["inv-007"])
    row = json.loads((tmp_path / "res" / CONFIG / "predictions.jsonl").read_text(encoding="utf-8"))
    assert row["status"] == "auto_approved" and row["fields"]["total"] is False
    assert row["key_fields_correct"] is False and row["issue_codes"] == []
    assert metrics["overall"]["precision_auto_approved"] == ratio(0, 1)
    assert metrics["overall"]["corrupted_recall"] == ratio(0, 1)
    assert metrics["overall"]["all_key_fields_correct"] == ratio(0, 1)


def test_ratio_and_percentile_helpers() -> None:
    assert ratio(1, 3) == {"value": 0.3333, "numerator": 1, "denominator": 3}
    assert ratio(0, 0) == {"value": None, "numerator": 0, "denominator": 0}
    assert percentile([], 0.95) is None
    assert percentile([5, 1, 3], 0.95) == 5
    assert percentile(list(range(1, 101)), 0.95) == 95
    assert percentile([7], 0.5) == 7
