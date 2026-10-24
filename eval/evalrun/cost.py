"""Cost guard for live runs and the cost report built from recordings."""

import json
import statistics
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from evalrun.configs import EvalConfig
from invoice_pilot.llm.pricing import cost_usd, price_for
from invoice_pilot.llm.types import UsageRecord

#: Upper bounds of (input, output) tokens per document and input type, used ONLY to
#: refuse a live run that could exceed ``--max-cost`` before the first call.
#: Derived in report 07 (2026-10-24) from the providers' documentation:
#:   Gemini: prompt ~580 + schema ~750 (counted pessimistically) + document text
#:   (<= 300) or media at the API default resolution (PDF 560 tokens/page, up to two
#:   pages; image 1120); output = JSON <= 400 + thinking at the lowest level.
#:   OpenAI: prompt + text; PDF pages and images at detail=auto (= high on current
#:   models): up to 2500 patches x 1.2 = 3000 tokens per page/image; detail=low
#:   fits an image into 512x512 (<= 307 tokens); output with reasoning effort none.
#: Real usage is expected to be well below; the pilot run measures it.
TOKEN_UPPER_BOUNDS: Final[dict[str, dict[str, tuple[int, int]]]] = {
    "gemini": {"text_pdf": (1700, 800), "scan_pdf": (2500, 800), "photo": (2500, 800)},
    "openai-auto": {"text_pdf": (1000, 600), "scan_pdf": (7000, 600), "photo": (3500, 600)},
    "openai-low": {"text_pdf": (1000, 600), "scan_pdf": (7000, 600), "photo": (1200, 600)},
}


def bounds_key(config: EvalConfig) -> str:
    if config.llm.provider == "gemini":
        return "gemini"
    return "openai-low" if config.llm.image_detail == "low" else "openai-auto"


def estimate_cost(config: EvalConfig, input_types: list[str], on_date: date) -> Decimal:
    """Worst-case USD for one call per document at list prices on ``on_date``."""
    bounds = TOKEN_UPPER_BOUNDS[bounds_key(config)]
    price = price_for(config.llm.model, on_date)
    total = Decimal(0)
    for kind in input_types:
        tokens_in, tokens_out = bounds[kind]
        total += (
            Decimal(tokens_in) * price.input_usd_per_1m
            + Decimal(tokens_out) * price.output_usd_per_1m
        ) / Decimal(1_000_000)
    return total


@dataclass(frozen=True, slots=True)
class RecordedUsage:
    """Usage of one recorded answer, priced on its recording date."""

    doc_id: str
    input_type: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    recorded_at: str
    cost_usd: Decimal


def load_recorded_usage(path: Path, doc_id: str, input_type: str, model: str) -> RecordedUsage:
    payload = json.loads(path.read_text(encoding="utf-8"))
    usage = payload["usage"]
    record = UsageRecord(
        provider=payload["provider"],
        model=model,
        status="ok",
        input_tokens=int(usage["input_tokens"]),
        output_tokens=int(usage["output_tokens"]),
        thinking_tokens=int(usage.get("thinking_tokens", 0)),
    )
    recorded_at = str(payload["recorded_at"])
    return RecordedUsage(
        doc_id=doc_id,
        input_type=input_type,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        thinking_tokens=record.thinking_tokens,
        recorded_at=recorded_at,
        cost_usd=cost_usd(record, date.fromisoformat(recorded_at[:10])),
    )


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-95 * len(ordered) // 100))  # ceil(0.95 n)
    return ordered[rank - 1]


def usage_table(usages: list[RecordedUsage]) -> list[dict[str, Any]]:
    """Rows per input type plus 'all': documents, mean/p95 tokens, cost per doc and per 1000."""
    rows = []
    groups = [("text_pdf", "scan_pdf", "photo")[i] for i in range(3)] + ["all"]
    for kind in groups:
        group = usages if kind == "all" else [u for u in usages if u.input_type == kind]
        if not group:
            rows.append({"input_type": kind, "documents": 0})
            continue
        total = sum((u.cost_usd for u in group), Decimal(0))
        per_doc = total / len(group)
        rows.append(
            {
                "input_type": kind,
                "documents": len(group),
                "input_mean": round(float(statistics.mean(u.input_tokens for u in group)), 1),
                "input_p95": _p95([u.input_tokens for u in group]),
                "output_mean": round(float(statistics.mean(u.output_tokens for u in group)), 1),
                "output_p95": _p95([u.output_tokens for u in group]),
                "thinking_mean": round(float(statistics.mean(u.thinking_tokens for u in group)), 1),
                "cost_per_document": per_doc.quantize(Decimal("0.000001")),
                "cost_per_1000": (per_doc * 1000).quantize(Decimal("0.01")),
                "cost_total": total.quantize(Decimal("0.000001")),
            }
        )
    return rows


def render_cost_report(sections: list[tuple[EvalConfig, list[RecordedUsage]]]) -> str:
    """Markdown: one table per configuration plus a comparison table."""
    lines = ["# LLM cost report", ""]
    lines.append(
        "Actual token usage from recorded answers (one successful call per document; "
        "failed attempts of a live run are not recorded). Prices: list price on the "
        "recording date (`invoice_pilot.llm.pricing`)."
    )
    lines.append("")
    comparison = []
    for config, usages in sections:
        rows = usage_table(usages)
        dates = sorted({u.recorded_at[:10] for u in usages})
        when = f"{dates[0]} .. {dates[-1]}" if dates else "no recordings"
        lines.append(f"## {config.name} ({config.llm.model})")
        lines.append("")
        lines.append(f"Recorded documents: {len(usages)}; recording dates: {when}.")
        lines.append("")
        lines.append(
            "| Input type | Docs | Input tokens mean | p95 | Output tokens mean | p95 "
            "| Thinking mean | Cost / doc | Cost / 1,000 |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for row in rows:
            if row["documents"] == 0:
                lines.append(f"| {row['input_type']} | 0 | | | | | | | |")
                continue
            lines.append(
                f"| {row['input_type']} | {row['documents']} | {row['input_mean']} | "
                f"{row['input_p95']} | {row['output_mean']} | {row['output_p95']} | "
                f"{row['thinking_mean']} | ${row['cost_per_document']} | ${row['cost_per_1000']} |"
            )
        lines.append("")
        overall = rows[-1]
        if overall["documents"]:
            lines.append(
                f"**1,000 invoices ~ ${overall['cost_per_1000']} on LLM calls** "
                f"({config.llm.model}, mix of the dataset: 40 % text PDF, 30 % scan, 30 % photo)."
            )
            lines.append("")
            comparison.append((config, overall))
    lines.append("## Comparison")
    lines.append("")
    lines.append(
        "| Configuration | Model | Docs | Input mean | Output mean | Cost / doc | Cost / 1,000 |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for config, overall in comparison:
        lines.append(
            f"| {config.name} | {config.llm.model} | {overall['documents']} | "
            f"{overall['input_mean']} | {overall['output_mean']} | "
            f"${overall['cost_per_document']} | ${overall['cost_per_1000']} |"
        )
    lines.append("")
    return "\n".join(lines)
