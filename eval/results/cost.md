# LLM cost report

Actual token usage from recorded answers (one successful call per document; failed attempts of a live run are not recorded). Prices: list price on the recording date (`invoice_pilot.llm.pricing`).

## gemini-flash-lite (gemini-3.5-flash-lite)

Recorded documents: 120; recording dates: 2026-10-30 .. 2026-10-30.

| Input type | Docs | Input tokens mean | p95 | Output tokens mean | p95 | Thinking mean | Cost / doc | Cost / 1,000 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| text_pdf | 48 | 992.8 | 1190 | 557.0 | 718 | 0.0 | $0.001690 | $1.69 |
| scan_pdf | 36 | 1187.8 | 1664 | 529.8 | 752 | 0.0 | $0.001681 | $1.68 |
| photo | 36 | 1689.9 | 1692 | 504.0 | 695 | 0.0 | $0.001767 | $1.77 |
| all | 120 | 1260.4 | 1692 | 532.9 | 718 | 0.0 | $0.001710 | $1.71 |

**1,000 invoices ~ $1.71 on LLM calls** (gemini-3.5-flash-lite, mix of the dataset: 40 % text PDF, 30 % scan, 30 % photo).

## Comparison

| Configuration | Model | Docs | Input mean | Output mean | Cost / doc | Cost / 1,000 |
|---|---|---:|---:|---:|---:|---:|
| gemini-flash-lite | gemini-3.5-flash-lite | 120 | 1260.4 | 532.9 | $0.001710 | $1.71 |
