# Example invoices

Three **synthetic** invoices copied unchanged from the evaluation set (`eval/dataset/docs/`,
dataset v1.0). The companies, people, addresses and numbers are invented, and none of the
files is a real document. Use them to try the bot, the HTTP API (`docs/api.md`) or the n8n
workflows (`examples/n8n/`).

| File | Input kind | Currency | Total printed | Expected result with the real model |
|---|---|---|---|---|
| `inv-001.pdf` | text PDF (1 page) | USD | 3,798.41 | `auto_approved`, no issues |
| `inv-005.jpg` | photo (JPEG) | JPY | 809,610 | `auto_approved`, no issues |
| `inv-007.pdf` | text PDF (1 page) | USD | 4,241.62 | `needs_review` with `total_mismatch`: the printed total is not subtotal + tax (3,992.58 + 249.54), on purpose |

The expected results are those of the ground truth in `eval/dataset/ground_truth/`.

With `LLM_FAKE=1` (no model key, no model call) the fake model knows these three files by
their SHA-256 and returns the fields printed on each one, so the results match the table:

| File | `LLM_FAKE=1` result |
|---|---|
| `inv-001.pdf` | `auto_approved`, no issues, total `3798.41` |
| `inv-005.jpg` | `auto_approved`, no issues, total `809610` |
| `inv-007.pdf` | `needs_review`, issue `total_mismatch` on `total`, total `4241.62` |

The digest is taken over what the model would receive: the extracted text of a text PDF,
the file bytes of a photo or a scan. Any other file, or an edited copy of these, gets the
`inv-001` answer. Only the input kind and the page count then come from the file itself.

```sh
curl -H "Authorization: Bearer $API_TOKEN" -F "file=@examples/invoices/inv-007.pdf" \
  http://127.0.0.1:8080/extract
```
