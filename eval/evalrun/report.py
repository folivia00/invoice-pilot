"""Human-readable outputs: summary.md per run and the README metrics block."""

import json
import math
import re
import statistics
from pathlib import Path
from typing import Any, Final

from evalrun.compare import KEY_FIELDS, TEXT_FIELDS
from evalrun.metrics import INPUT_TYPES, percentile

README_START: Final = "<!-- eval:start -->"
README_END: Final = "<!-- eval:end -->"
WILSON_Z: Final = 1.96  # 95 % two-sided
INPUT_TYPE_LABELS: Final = {"text_pdf": "Text PDF", "scan_pdf": "Scan (PDF)", "photo": "Photo"}
CI_NOTE: Final = "95 % Wilson score interval; small groups give wide intervals."


def pct(item: dict[str, Any]) -> str:
    """'97.5 % (117/120)' or 'n/a (0/0)'."""
    if item["value"] is None:
        return f"n/a ({item['numerator']}/{item['denominator']})"
    return f"{item['value'] * 100:.1f} % ({item['numerator']}/{item['denominator']})"


def wilson_interval(k: int, n: int) -> tuple[float, float] | None:
    """95 % Wilson score interval for k successes out of n, as fractions; None when n = 0."""
    if n == 0:
        return None
    z2 = WILSON_Z * WILSON_Z
    p = k / n
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = WILSON_Z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def ci_text(k: int, n: int) -> str:
    """'91.9–99.0' (percent, one decimal) or 'n/a' when n = 0."""
    interval = wilson_interval(k, n)
    if interval is None:
        return "n/a"
    low, high = interval
    return f"{low * 100:.1f}–{high * 100:.1f}"


def pct_ci(item: dict[str, Any]) -> str:
    """'97.1 % (101/104; 95 % CI 91.9–99.0)' or 'n/a (0/0)'."""
    if item["value"] is None:
        return pct(item)
    k, n = item["numerator"], item["denominator"]
    return f"{item['value'] * 100:.1f} % ({k}/{n}; 95 % CI {ci_text(k, n)})"


def latency_stats(predictions: list[dict[str, Any]]) -> tuple[float, int, int] | None:
    """(median ms, p95 ms by nearest rank, number of calls) over successful model calls."""
    latencies = [
        usage["latency_ms"]
        for row in predictions
        for usage in row["usage"]
        if usage["status"] == "ok"
    ]
    p95 = percentile(latencies, 0.95)
    if p95 is None:
        return None
    return statistics.median(latencies), p95, len(latencies)


def load_runs(results_dir: Path) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """(metrics.json, predictions.jsonl rows) of every configuration folder, sorted by name."""
    runs = []
    for path in sorted(results_dir.glob("*/metrics.json")):
        metrics = json.loads(path.read_text(encoding="utf-8"))
        rows = (path.parent / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
        runs.append((metrics, [json.loads(row) for row in rows if row]))
    return runs


def _group_rows(title: str, groups: dict[str, dict[str, Any]]) -> list[str]:
    lines = [
        f"| {title} | Docs | All key fields correct | Auto-approved | Precision among "
        "auto-approved | Corrupted caught | Caught with expected code | Clean sent to review |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, g in groups.items():
        lines.append(
            f"| {name} | {g['documents']} | {pct(g['all_key_fields_correct'])} | "
            f"{pct(g['auto_approved'])} | {pct(g['precision_auto_approved'])} | "
            f"{pct(g['corrupted_recall'])} | {pct(g['corrupted_recall_expected_code'])} | "
            f"{pct(g['clean_sent_to_review'])} |"
        )
    return lines


def render_summary(metrics: dict[str, Any]) -> str:
    """summary.md for one configuration run (deterministic: no clock, no paths)."""
    run = metrics["run"]
    overall = metrics["overall"]
    cost = metrics["cost"]["overall"]
    lines = [
        f"# Evaluation summary: {run['config']}",
        "",
        f"- Model: `{run['model']}` ({run['provider']}), parameters: "
        + ", ".join(f"{k}={v}" for k, v in sorted(run["params"].items())),
        f"- Dataset: v{run['dataset_version']} ({run['documents_selected']} documents selected, "
        f"{metrics['documents_evaluated']} evaluated), reference date {run['reference_date']}",
        f"- Prompt: {run['prompt_version']}, schema sha256 {run['schema_sha256'][:12]}, "
        f"methodology v{run['methodology_version']}",
        f"- Recordings: {run['recordings_first'] or 'none'} .. {run['recordings_last'] or 'none'}"
        f" (run date {run['run_date'] or 'n/a'})",
        "",
        "## Main results",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Precision among auto-approved (main) | {pct(overall['precision_auto_approved'])} |",
        f"| All key fields correct (model only) | {pct(overall['all_key_fields_correct'])} |",
        f"| Auto-approved | {pct(overall['auto_approved'])} |",
        f"| Needs review | {pct(overall['needs_review'])} |",
        f"| Corrupted documents caught | {pct(overall['corrupted_recall'])} |",
        f"| Corrupted caught with the expected issue code | "
        f"{pct(overall['corrupted_recall_expected_code'])} |",
        f"| Clean documents sent to review | {pct(overall['clean_sent_to_review'])} |",
        f"| Extraction failed | {metrics['extraction_failed']} |",
        f"| Provider errors | {metrics['provider_errors']} |",
        f"| Missing recordings | {len(metrics['missing_recordings'])} |",
        f"| Model retries | {metrics['model_retries']} |",
        "",
        "Model only vs model + checks: "
        f"{pct(metrics['model_only_vs_with_checks']['model_only'])} of all documents have every "
        f"key field right; among auto-approved documents it is "
        f"{pct(metrics['model_only_vs_with_checks']['model_with_checks'])}.",
        "",
        "## Field accuracy",
        "",
        "| Key field | Correct |",
        "|---|---:|",
    ]
    for name in KEY_FIELDS:
        lines.append(f"| {name} | {pct(metrics['field_accuracy']['key'][name])} |")
    lines += ["", "| Free-text field (not in the main metrics) | Correct |", "|---|---:|"]
    for name in TEXT_FIELDS:
        lines.append(f"| {name} | {pct(metrics['field_accuracy']['text'][name])} |")
    lines += ["", "## By input type", ""]
    lines += _group_rows("Input type", metrics["by_input_type"])
    lines += ["", "## By language", ""]
    lines += _group_rows("Language", metrics["by_language"])
    lines += [
        "",
        "## Cost",
        "",
        "| Input type | Docs | Input tokens mean | p95 | Output tokens mean | p95 | "
        "Cost / doc | Cost / 1,000 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for kind, stats in [("all", cost), *metrics["cost"]["by_input_type"].items()]:
        if stats["documents"] == 0:
            lines.append(f"| {kind} | 0 | | | | | | |")
            continue
        lines.append(
            f"| {kind} | {stats['documents']} | {stats['input_tokens']['mean']} | "
            f"{stats['input_tokens']['p95']} | {stats['output_tokens']['mean']} | "
            f"{stats['output_tokens']['p95']} | ${stats['cost_usd_per_document']} | "
            f"${stats['cost_usd_per_1000']} |"
        )
    if cost["documents"]:
        lines += ["", f"**1,000 invoices ~ ${cost['cost_usd_per_1000']} on LLM calls.**"]
    if metrics["missing_recordings"]:
        lines += ["", "## Missing recordings", ""]
        lines.append(", ".join(metrics["missing_recordings"]))
    lines.append("")
    return "\n".join(lines)


def _run_notes(metrics: dict[str, Any], predictions: list[dict[str, Any]]) -> list[str]:
    overall = metrics["overall"]
    latency = latency_stats(predictions)
    latency_line = (
        f"Model call latency: median {latency[0] / 1000:.1f} s, p95 {latency[1] / 1000:.1f} s "
        f"({latency[2]} recorded calls, from `predictions.jsonl`)."
        if latency
        else "Model call latency: n/a (no recorded calls)."
    )
    return [
        f"Corrupted invoices caught: {pct_ci(overall['corrupted_recall'])}; with the expected "
        f"issue code: {pct(overall['corrupted_recall_expected_code'])}. Clean invoices sent "
        f"to review: {pct(overall['clean_sent_to_review'])}.",
        latency_line,
        f"Run of {metrics['run']['run_date'] or 'n/a'}. Cost uses the actual token counts at "
        "paid-tier list prices.",
    ]


def _input_type_rows(metrics: dict[str, Any]) -> list[str]:
    lines = [
        "| Input type | Docs | Auto-approved | Precision among auto-approved | "
        "All key fields correct (model only) | Corrupted caught |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for kind in INPUT_TYPES:
        g = metrics["by_input_type"][kind]
        auto = g["auto_approved"]
        lines.append(
            f"| {INPUT_TYPE_LABELS[kind]} | {g['documents']} | "
            f"{auto['numerator']}/{auto['denominator']} | {pct_ci(g['precision_auto_approved'])} | "
            f"{pct(g['all_key_fields_correct'])} | {pct(g['corrupted_recall'])} |"
        )
    return lines


def render_readme_block(runs: list[tuple[dict[str, Any], list[dict[str, Any]]]]) -> str:
    """The README results block: headline table, run notes and the input-type breakdown.

    ``runs`` holds (metrics.json, predictions.jsonl rows) per configuration, as
    returned by ``load_runs``. Intervals are computed here from the counts.
    """
    lines = [
        README_START,
        "| Configuration | Model | Docs | Precision among auto-approved | Auto-approved | "
        "All key fields correct (model only) | Corrupted caught | Cost / 1,000 invoices |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for metrics, _ in runs:
        run, overall = metrics["run"], metrics["overall"]
        cost = metrics["cost"]["overall"]["cost_usd_per_1000"]
        lines.append(
            f"| {run['config']} | {run['model']} | {metrics['documents_evaluated']} | "
            f"{pct_ci(overall['precision_auto_approved'])} | {pct(overall['auto_approved'])} | "
            f"{pct(overall['all_key_fields_correct'])} | {pct(overall['corrupted_recall'])} | "
            f"{'$' + cost if cost else 'n/a'} |"
        )
    for metrics, predictions in runs:
        prefix = f"`{metrics['run']['config']}`: " if len(runs) > 1 else ""
        lines.append("")
        lines += [f"- {prefix}{note}" for note in _run_notes(metrics, predictions)]
    for metrics, _ in runs:
        lines += ["", f"**By input type** ({metrics['run']['config']}):", ""]
        lines += _input_type_rows(metrics)
    lines += ["", CI_NOTE, README_END]
    return "\n".join(lines)


_BLOCK_RE: Final = re.compile(
    re.escape(README_START) + r".*?" + re.escape(README_END), flags=re.DOTALL
)


def extract_readme_block(text: str) -> str | None:
    match = _BLOCK_RE.search(text)
    return match.group(0) if match else None


def readme_block_matches(path: Path, expected_block: str) -> bool:
    """True when the block between the markers in ``path`` equals ``expected_block``."""
    return extract_readme_block(path.read_text(encoding="utf-8")) == expected_block


def replace_readme_block(text: str, block: str) -> str:
    """Return ``text`` with the marked block replaced (ValueError if markers are absent)."""
    if _BLOCK_RE.search(text) is None:
        raise ValueError("README markers not found")
    return _BLOCK_RE.sub(lambda _: block, text, count=1)
