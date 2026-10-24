"""Human-readable outputs: summary.md per run and the README metrics block."""

import re
from pathlib import Path
from typing import Any, Final

from evalrun.compare import KEY_FIELDS, TEXT_FIELDS

README_START: Final = "<!-- eval:start -->"
README_END: Final = "<!-- eval:end -->"


def pct(item: dict[str, Any]) -> str:
    """'97.5 % (117/120)' or 'n/a (0/0)'."""
    if item["value"] is None:
        return f"n/a ({item['numerator']}/{item['denominator']})"
    return f"{item['value'] * 100:.1f} % ({item['numerator']}/{item['denominator']})"


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


def render_readme_block(metrics_list: list[dict[str, Any]]) -> str:
    """Markdown table of the headline numbers, one row per configuration."""
    lines = [
        README_START,
        "| Configuration | Model | Docs | Precision among auto-approved | Auto-approved | "
        "All key fields correct (model only) | Corrupted caught | Cost / 1,000 invoices |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for metrics in metrics_list:
        run, overall = metrics["run"], metrics["overall"]
        cost = metrics["cost"]["overall"]["cost_usd_per_1000"]
        lines.append(
            f"| {run['config']} | {run['model']} | {metrics['documents_evaluated']} | "
            f"{pct(overall['precision_auto_approved'])} | {pct(overall['auto_approved'])} | "
            f"{pct(overall['all_key_fields_correct'])} | {pct(overall['corrupted_recall'])} | "
            f"{'$' + cost if cost else 'n/a'} |"
        )
    lines.append(README_END)
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
