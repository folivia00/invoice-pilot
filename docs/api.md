# HTTP API

`python -m invoice_pilot.api` runs the same pipeline as the Telegram bot behind two HTTP
endpoints: daily limits, the processing queue, the request-rate limit, the model call,
deterministic validation and storage with retention. It is a separate process with its own
database (one process per database, see the README). It is meant for the Full profile:
Docker at the client (`docker compose up api`) or a local run. The Compact demo server
exposes no ports.

```sh
uv run alembic upgrade head
API_TOKEN=change-me uv run python -m invoice_pilot.api     # 127.0.0.1:8080
```

Settings: `API_TOKEN` (required: without it the process refuses to start and names the
variable), `API_HOST`, `API_PORT`, `API_DAILY_DOCS` and the common settings in the README.
`LLM_FAKE=1` replaces the model with one built-in synthetic answer, for checks without keys.

## `GET /health`

No authentication. Returns `200 {"status": "ok"}` and nothing about the configuration.

## `POST /extract`

- Header `Authorization: Bearer <API_TOKEN>` (compared in constant time).
- Body `multipart/form-data` with one field `file`: a PDF, JPEG or PNG invoice. The file
  type is taken from the file signature, not from the declared content type.
- Limits come from the profile: `compact` 5 MB, `full` 10 MB; at most 5 pages.
- The file stays in memory and is never written to disk.
- `API_DAILY_DOCS` documents per UTC day, plus the global document and budget limits shared
  with nothing else in this process.

```sh
curl -H "Authorization: Bearer $API_TOKEN" \
  -F "file=@examples/invoices/inv-007.pdf" \
  http://127.0.0.1:8080/extract
```

### `200 OK`

| Field | Meaning |
|---|---|
| `document_id` | ID of the stored result (deleted after `RETENTION_DAYS`) |
| `status` | `auto_approved` (all checks passed) or `needs_review` |
| `issues` | Validation findings: `code`, `field`, `severity` (`error` or `warning`), `message` |
| `invoice` | Extracted fields; `null` if the model gave no usable answer (`extraction_failed`) |
| `input_kind` | `text_pdf` (text layer sent), `scan_pdf` or `photo` (file sent to the model) |
| `pages` | Pages of the PDF (1 for an image) |
| `model` | Model that answered |
| `processing_ms` | Time spent in the model call(s) |

Amounts, quantities and rates are JSON **strings** holding exact decimals as returned by the
model, for example `"4241.62"` or `"320"`. They are never floats. Dates are ISO 8601.

Example: the synthetic `inv-007.pdf`, whose printed total does not match subtotal + tax.

```json
{
  "document_id": 12,
  "status": "needs_review",
  "issues": [
    {
      "code": "total_mismatch",
      "field": "total",
      "severity": "error",
      "message": "Total 4241.62 does not equal subtotal + tax (3992.58 + 249.54 = 4242.12) (exact match required)."
    }
  ],
  "invoice": {
    "supplier": {"name": "Greywater Plumbing LLC", "tax_id": "38-6726486", "address": "571 Harbor View Lane, Denver 93060"},
    "buyer": {"name": "Lantern Hill Books Inc.", "tax_id": null, "address": "522 Harbor View Lane, Tucson 42451"},
    "invoice_number": "RE-202694453",
    "invoice_date": "2026-01-03",
    "due_date": "2026-01-10",
    "currency": "USD",
    "line_items": [
      {"description": "Courier delivery", "quantity": "1", "unit_price": "46.00", "amount": "46.00"},
      {"description": "Printer toner cartridge", "quantity": "4", "unit_price": "47.53", "amount": "190.12"}
    ],
    "subtotal": "3992.58",
    "tax_rate": "6.25",
    "tax_amount": "249.54",
    "total": "4241.62",
    "iban": null,
    "bic": null
  },
  "input_kind": "text_pdf",
  "pages": 1,
  "model": "gemini-3.5-flash-lite",
  "processing_ms": 1673
}
```

(Shortened: the invoice has five line items. `document_id` and `processing_ms` vary.)

### Errors

Every error is JSON: `{"error": {"code": "...", "message": "..."}}`.

| Status | `code` | When |
|---|---|---|
| 400 | `missing_file` | Not `multipart/form-data`, no `file` field, or an empty file |
| 400 | `unsupported_file` | Not a PDF, JPEG or PNG (by file signature) |
| 400 | `invalid_file` | Unreadable or password-protected PDF, or more pages than the limit; the message says which |
| 401 | `unauthorized` | Missing or wrong bearer token (header `WWW-Authenticate: Bearer`) |
| 413 | `file_too_large` | Larger than the profile limit. It is checked from `Content-Length` before reading, and the read stops at the limit for chunked uploads |
| 429 | `daily_limit` | `API_DAILY_DOCS`, the global daily documents or the daily budget is used up (UTC day) |
| 503 | `provider_unavailable` | The model API failed after retries (`Retry-After: 60`). The document does not count toward the limit |
| 503 | `queue_full` | Too many documents waiting for a processing slot (`Retry-After: 60`) |
| 500 | `internal_error` | Unexpected failure; the document does not count toward the limit |

## Logs

One INFO line per `/extract` request: HTTP status, input kind, validation status, latency
and cost. For processed documents there is also the pipeline's `document processed` line.
Never logged: the token, client addresses (the aiohttp access log is off), file names,
document content or extracted fields.
