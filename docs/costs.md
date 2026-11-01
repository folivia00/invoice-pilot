# LLM costs

Costs come from the **actual token usage** reported by the API for each recorded answer
of the evaluation (`eval/results/cost.md`, rebuilt by `uv run python eval/cost_report.py`).
They are priced at **paid-tier list prices**. The measurement itself ran on the Gemini
API free tier on 2026-10-30, so it cost nothing; at paid-tier prices the 120 documents
would cost $0.21.

## gemini-3.5-flash-lite, dataset v1.0 (120 documents)

| Input type | Docs | Input tokens mean | p95 | Output tokens mean | p95 | Cost / doc | Cost / 1,000 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Text PDF (extracted text) | 48 | 992.8 | 1190 | 557.0 | 718 | $0.001690 | $1.69 |
| Scanned PDF (file bytes) | 36 | 1187.8 | 1664 | 529.8 | 752 | $0.001681 | $1.68 |
| Photo (file bytes) | 36 | 1689.9 | 1692 | 504.0 | 695 | $0.001767 | $1.77 |
| All | 120 | 1260.4 | 1692 | 532.9 | 718 | $0.001710 | $1.71 |

**1,000 invoices ≈ $1.71 on LLM calls** for the dataset mix (40 % text PDF, 30 % scan,
30 % photo). Thinking tokens: 0 (the lowest thinking level the model accepts).

Output tokens make up most of the cost: about 533 output tokens at $2.50 per million,
against about 1,260 input tokens at $0.30 per million.

## Prices

| Model | Input, USD per 1M tokens | Output, USD per 1M tokens | Source | Checked |
|---|---:|---:|---|---|
| gemini-3.5-flash-lite | 0.30 | 2.50 | [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) | 2026-10-24 |

Prices live in `src/invoice_pilot/llm/pricing.py` with their source and the date they
were checked. Each call is priced at the price valid on its date.

## Caveats

- **Free tier.** The demo and the measurement use the Gemini API free tier, where Google
  may use inputs to improve its products. Only synthetic documents are processed. Moving
  to the paid tier is a separate decision; the costs above are what it would charge.
- **Retries.** A replay prices the one successful call per document. A live run that
  needed a retry also pays for the failed attempt, which is not included here. In the
  recorded run no document needed a model retry.
- **Synthetic documents.** Real invoices with more lines or pages produce more tokens.
- **Demo budget.** The demo caps LLM spend per UTC day (`GLOBAL_DAILY_BUDGET_USD`, default
  $0.50), priced the same way.
