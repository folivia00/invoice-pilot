"""Eval numbers in docs/: architecture.md links to the README, costs.md matches the results."""

import json
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from invoice_pilot.llm.pricing import PRICES

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "eval" / "results"
ARCHITECTURE = REPO / "docs" / "architecture.md"
COSTS = REPO / "docs" / "costs.md"

# docs/costs.md row label -> key in metrics.json "cost" (eval/results/cost.md uses the keys).
COST_ROWS = {
    "Text PDF (extracted text)": "text_pdf",
    "Scanned PDF (file bytes)": "scan_pdf",
    "Photo (file bytes)": "photo",
    "All": "all",
}


def _metrics() -> dict[str, Any]:
    path = RESULTS / "gemini-flash-lite" / "metrics.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _table_rows(text: str) -> dict[str, list[str]]:
    rows = {}
    for line in text.splitlines():
        if line.startswith("| ") and not line.startswith("|---"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            rows[cells[0]] = cells[1:]
    return rows


def _expected_cost_cells(stats: dict[str, Any]) -> list[str]:
    return [
        str(stats["documents"]),
        str(stats["input_tokens"]["mean"]),
        str(stats["input_tokens"]["p95"]),
        str(stats["output_tokens"]["mean"]),
        str(stats["output_tokens"]["p95"]),
        f"${stats['cost_usd_per_document']}",
        f"${stats['cost_usd_per_1000']}",
    ]


def test_architecture_links_to_the_readme_instead_of_eval_numbers() -> None:
    text = ARCHITECTURE.read_text(encoding="utf-8")
    assert "](../README.md#results)" in text
    assert "%" not in text
    metrics = _metrics()
    groups = [metrics["overall"], *metrics["by_input_type"].values()]
    for group in groups:
        for item in group.values():
            if not isinstance(item, dict) or item["value"] is None:
                continue
            for number in (
                f"{item['value'] * 100:.1f}",
                f"{item['numerator']}/{item['denominator']}",
            ):
                assert re.search(rf"(?<![\d.]){re.escape(number)}(?![\d.])", text) is None, number
    counted = re.compile(r"\b\d+\s+(?:of the \d+\s+)?(?:corrupted|auto-approved|invoices)\b")
    assert counted.search(text) is None


def test_costs_table_matches_the_results() -> None:
    cost = _metrics()["cost"]
    by_key = {"all": cost["overall"], **cost["by_input_type"]}
    docs_rows = _table_rows(COSTS.read_text(encoding="utf-8"))
    report_rows = _table_rows((RESULTS / "cost.md").read_text(encoding="utf-8"))
    for label, key in COST_ROWS.items():
        expected = _expected_cost_cells(by_key[key])
        assert docs_rows[label] == expected, label
        # cost.md has an extra "Thinking mean" column before the cost columns.
        report = report_rows[key]
        assert report[:5] + report[6:] == expected, key


def test_costs_totals_and_prices_match_the_results() -> None:
    metrics = _metrics()
    overall = metrics["cost"]["overall"]
    text = COSTS.read_text(encoding="utf-8")
    assert f"**1,000 invoices ≈ ${overall['cost_usd_per_1000']} on LLM calls**" in text
    total = Decimal(overall["cost_usd_total"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert f"the {overall['documents']} documents\nwould cost ${total}." in text
    docs = {k: v["documents"] for k, v in metrics["cost"]["by_input_type"].items()}
    share = {k: round(100 * n / overall["documents"]) for k, n in docs.items()}
    assert (
        f"({share['text_pdf']} % text PDF, {share['scan_pdf']} % scan,\n{share['photo']} % photo)"
    ) in text
    output_mean = round(overall["output_tokens"]["mean"])
    input_mean = round(overall["input_tokens"]["mean"])
    assert f"about {output_mean} output tokens" in text
    assert f"about {input_mean:,} input tokens" in text
    run_date = date.fromisoformat(metrics["run"]["run_date"])
    price = next(p for p in PRICES if p.model == metrics["run"]["model"] and p.applies_on(run_date))
    inp, out = price.input_usd_per_1m, price.output_usd_per_1m
    assert f"| {price.model} | {inp} | {out} | [Gemini API pricing]({price.source}) | " in text
    assert f"| {price.checked_on.isoformat()} |" in text
    assert f"${out} per million" in text
    assert f"${inp} per million" in text
