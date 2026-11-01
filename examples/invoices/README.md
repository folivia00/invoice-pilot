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

The expected results are those of the ground truth in `eval/dataset/ground_truth/`. With
`LLM_FAKE=1`, every file gets the same built-in synthetic answer (the fields of `inv-001`,
`auto_approved`) whatever its content. Only the input kind and the page count come from the
file itself.

```sh
curl -H "Authorization: Bearer $API_TOKEN" -F "file=@examples/invoices/inv-007.pdf" \
  http://127.0.0.1:8080/extract
```
