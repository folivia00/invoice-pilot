"""Aggregate per-document predictions into the metrics of docs/evaluation.md.

A prediction is the dict written to ``predictions.jsonl`` (see ``runner``).
Documents without a verdict (missing recording, provider error) are counted
but excluded from every ratio's denominator.
"""

import math
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from evalrun.compare import KEY_FIELDS, TEXT_FIELDS

EVALUATED_STATUSES = ("auto_approved", "needs_review")
INPUT_TYPES = ("text_pdf", "scan_pdf", "photo")
LANGUAGES = ("en", "de")


def ratio(numerator: int, denominator: int) -> dict[str, Any]:
    """Fraction with its counts; value is None when the denominator is zero."""
    value = round(numerator / denominator, 4) if denominator else None
    return {"value": value, "numerator": numerator, "denominator": denominator}


def group_metrics(preds: list[dict[str, Any]]) -> dict[str, Any]:
    """Metrics 2-6 of the methodology for one slice of evaluated documents."""
    auto = [p for p in preds if p["status"] == "auto_approved"]
    review = [p for p in preds if p["status"] == "needs_review"]
    corrupted = [p for p in preds if p["corruption"]]
    clean = [p for p in preds if not p["corruption"]]
    caught = [p for p in corrupted if p["status"] == "needs_review"]
    caught_with_code = [
        p for p in caught if set(p["expected_issue_codes"]) <= set(p["issue_codes"])
    ]
    return {
        "documents": len(preds),
        "all_key_fields_correct": ratio(
            sum(1 for p in preds if p["key_fields_correct"]), len(preds)
        ),
        "auto_approved": ratio(len(auto), len(preds)),
        "needs_review": ratio(len(review), len(preds)),
        "precision_auto_approved": ratio(
            sum(1 for p in auto if p["key_fields_correct"]), len(auto)
        ),
        "corrupted_recall": ratio(len(caught), len(corrupted)),
        "corrupted_recall_expected_code": ratio(len(caught_with_code), len(corrupted)),
        "clean_sent_to_review": ratio(
            sum(1 for p in clean if p["status"] == "needs_review"), len(clean)
        ),
    }


def field_accuracy(preds: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Share of evaluated documents where each field is correct (key and text fields apart)."""
    return {
        "key": {f: ratio(sum(1 for p in preds if p["fields"][f]), len(preds)) for f in KEY_FIELDS},
        "text": {
            f: ratio(sum(1 for p in preds if p["fields"][f]), len(preds)) for f in TEXT_FIELDS
        },
    }


def percentile(values: Iterable[int], share: float) -> int | None:
    """Nearest-rank percentile (p95: the value at rank ceil(0.95 n))."""
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, math.ceil(share * len(ordered)))
    return ordered[rank - 1]


def _mean(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def cost_metrics(preds: list[dict[str, Any]]) -> dict[str, Any]:
    """Token and cost statistics per input type and overall, from actual usage."""
    with_usage = [p for p in preds if p["usage"]]

    def stats(group: list[dict[str, Any]]) -> dict[str, Any]:
        inputs = [p["input_tokens"] for p in group]
        outputs = [p["output_tokens"] for p in group]
        costs = [Decimal(p["cost_usd"]) for p in group]
        total = sum(costs, Decimal(0))
        per_doc = (total / len(group)) if group else None
        return {
            "documents": len(group),
            "input_tokens": {"mean": _mean(inputs), "p95": percentile(inputs, 0.95)},
            "output_tokens": {"mean": _mean(outputs), "p95": percentile(outputs, 0.95)},
            "thinking_tokens": {"mean": _mean([p["thinking_tokens"] for p in group])},
            "cost_usd_total": str(total),
            "cost_usd_per_document": _money(per_doc, "0.000001"),
            "cost_usd_per_1000": _money(per_doc * 1000, "0.01") if per_doc is not None else None,
        }

    return {
        "overall": stats(with_usage),
        "by_input_type": {
            kind: stats([p for p in with_usage if p["input_type"] == kind]) for kind in INPUT_TYPES
        },
    }


def _money(value: Decimal | None, step: str) -> str | None:
    return None if value is None else str(value.quantize(Decimal(step)))


def compute_metrics(preds: list[dict[str, Any]]) -> dict[str, Any]:
    """All metrics for one configuration run."""
    evaluated = [p for p in preds if p["status"] in EVALUATED_STATUSES]
    return {
        "documents_selected": len(preds),
        "documents_evaluated": len(evaluated),
        "missing_recordings": sorted(p["id"] for p in preds if p["status"] == "missing_recording"),
        "provider_errors": sum(1 for p in preds if p["status"] == "provider_error"),
        "extraction_failed": sum(1 for p in evaluated if "extraction_failed" in p["issue_codes"]),
        "model_retries": sum(max(p["attempts"] - 1, 0) for p in evaluated),
        "field_accuracy": field_accuracy(evaluated),
        "overall": group_metrics(evaluated),
        "by_input_type": {
            kind: group_metrics([p for p in evaluated if p["input_type"] == kind])
            for kind in INPUT_TYPES
        },
        "by_language": {
            lang: group_metrics([p for p in evaluated if p["language"] == lang])
            for lang in LANGUAGES
        },
        "model_only_vs_with_checks": {
            "model_only": group_metrics(evaluated)["all_key_fields_correct"],
            "model_with_checks": group_metrics(evaluated)["precision_auto_approved"],
        },
        "cost": cost_metrics(evaluated),
    }
