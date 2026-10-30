# Evaluation summary: gemini-flash-lite

- Model: `gemini-3.5-flash-lite` (gemini), parameters: media_resolution=default, thinking_level=minimal
- Dataset: v1.0 (120 documents selected, 120 evaluated), reference date 2026-10-01
- Prompt: extraction-v1, schema sha256 a90fb4433f7f, methodology v1.0
- Recordings: 2026-10-30T20:30:17+00:00 .. 2026-10-30T20:38:56+00:00 (run date 2026-10-30)

## Main results

| Metric | Value |
|---|---:|
| Precision among auto-approved (main) | 97.1 % (101/104) |
| All key fields correct (model only) | 96.7 % (116/120) |
| Auto-approved | 86.7 % (104/120) |
| Needs review | 13.3 % (16/120) |
| Corrupted documents caught | 100.0 % (15/15) |
| Corrupted caught with the expected issue code | 100.0 % (15/15) |
| Clean documents sent to review | 0.9 % (1/105) |
| Extraction failed | 0 |
| Provider errors | 0 |
| Missing recordings | 0 |
| Model retries | 0 |

Model only vs model + checks: 96.7 % (116/120) of all documents have every key field right; among auto-approved documents it is 97.1 % (101/104).

## Field accuracy

| Key field | Correct |
|---|---:|
| invoice_number | 100.0 % (120/120) |
| invoice_date | 98.3 % (118/120) |
| due_date | 99.2 % (119/120) |
| currency | 100.0 % (120/120) |
| subtotal | 100.0 % (120/120) |
| tax_rate | 100.0 % (120/120) |
| tax_amount | 100.0 % (120/120) |
| total | 100.0 % (120/120) |
| iban | 100.0 % (120/120) |
| bic | 100.0 % (120/120) |
| supplier.name | 99.2 % (119/120) |
| supplier.tax_id | 100.0 % (120/120) |
| buyer.name | 100.0 % (120/120) |
| buyer.tax_id | 100.0 % (120/120) |
| line_items | 99.2 % (119/120) |

| Free-text field (not in the main metrics) | Correct |
|---|---:|
| supplier.address | 47.5 % (57/120) |
| buyer.address | 84.2 % (101/120) |
| line_items.description | 99.2 % (119/120) |

## By input type

| Input type | Docs | All key fields correct | Auto-approved | Precision among auto-approved | Corrupted caught | Caught with expected code | Clean sent to review |
|---|---:|---:|---:|---:|---:|---:|---:|
| text_pdf | 48 | 97.9 % (47/48) | 85.4 % (41/48) | 100.0 % (41/41) | 100.0 % (6/6) | 100.0 % (6/6) | 2.4 % (1/42) |
| scan_pdf | 36 | 94.4 % (34/36) | 86.1 % (31/36) | 93.5 % (29/31) | 100.0 % (5/5) | 100.0 % (5/5) | 0.0 % (0/31) |
| photo | 36 | 97.2 % (35/36) | 88.9 % (32/36) | 96.9 % (31/32) | 100.0 % (4/4) | 100.0 % (4/4) | 0.0 % (0/32) |

## By language

| Language | Docs | All key fields correct | Auto-approved | Precision among auto-approved | Corrupted caught | Caught with expected code | Clean sent to review |
|---|---:|---:|---:|---:|---:|---:|---:|
| en | 72 | 95.8 % (69/72) | 86.1 % (62/72) | 96.8 % (60/62) | 100.0 % (9/9) | 100.0 % (9/9) | 1.6 % (1/63) |
| de | 48 | 97.9 % (47/48) | 87.5 % (42/48) | 97.6 % (41/42) | 100.0 % (6/6) | 100.0 % (6/6) | 0.0 % (0/42) |

## Cost

| Input type | Docs | Input tokens mean | p95 | Output tokens mean | p95 | Cost / doc | Cost / 1,000 |
|---|---:|---:|---:|---:|---:|---:|---:|
| all | 120 | 1260.4 | 1692 | 532.9 | 718 | $0.001710 | $1.71 |
| text_pdf | 48 | 992.8 | 1190 | 557.0 | 718 | $0.001690 | $1.69 |
| scan_pdf | 36 | 1187.8 | 1664 | 529.8 | 752 | $0.001681 | $1.68 |
| photo | 36 | 1689.9 | 1692 | 504.0 | 695 | $0.001767 | $1.77 |

**1,000 invoices ~ $1.71 on LLM calls.**
