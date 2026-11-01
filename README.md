# invoice-pilot

Telegram bot that extracts invoice data with an LLM, validates it with deterministic rules and sends doubtful documents to human review.

Status: work in progress (v1).

## Development

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest
uv run ruff check .
```

The `Makefile` is an optional wrapper around the same `uv run` commands (`make lint`, `make format`, `make test`).

## Running the bot

```sh
uv run alembic upgrade head      # create or migrate the SQLite database in DATA_DIR
uv run python -m invoice_pilot.bot
```

The bot uses long polling: no incoming ports or webhooks. Uploaded files are processed in memory and never written to disk; extracted fields and call metadata are deleted after `RETENTION_DAYS`.

**Privacy.** The demo runs on the Gemini API free tier, where Google may use inputs to improve its products. Do not upload real or confidential documents. Costs reported by this project are computed from actual token usage at paid-tier list prices.

## HTTP API, Docker and n8n

`python -m invoice_pilot.api` serves `POST /extract` with the same pipeline as the bot
(bearer token, JSON errors): see [docs/api.md](docs/api.md). It is a separate process with
its own database. The demo server runs only the bot and exposes no ports.

```sh
# .env with API_TOKEN (and GEMINI_API_KEY, or LLM_FAKE=1 to try it without keys)
docker compose up api          # http://127.0.0.1:8080
docker compose up bot          # the Telegram bot, with its own volume
```

The image (`Dockerfile`, profile `full`) installs the same hashed wheels as the server and
runs as a non-root user. Ready-made n8n workflows, a local one and a Gmail to Google Sheets
template, are in [examples/n8n](examples/n8n/). Synthetic sample invoices are in
[examples/invoices](examples/invoices/).

## Configuration

Settings are read from environment variables (see `.env.example`).

Daily limits, the queue and the request-rate limit are enforced inside one bot process. Run a single process per database, in the `full` profile too: the bot and the HTTP API each need their own `DATA_DIR`.

| Variable | Default | Meaning |
|---|---|---|
| `BOT_TOKEN` | required for the bot | Telegram bot token (secret) |
| `GEMINI_API_KEY` | required | Gemini API key (secret); not needed with `LLM_FAKE=1` |
| `PROFILE` | `compact` | `compact`: 5 MB files, one document at a time; `full`: 10 MB, 4 in parallel; up to 5 pages in both |
| `DATA_DIR` | `./data` | Directory of the SQLite database |
| `ADMIN_IDS` | empty | Comma-separated Telegram user IDs with admin rights |
| `LLM_MODEL` | `gemini-3.5-flash-lite` | Gemini model (must have a configured price) |
| `LLM_RPM` | `14` | Maximum model requests per minute, retries included |
| `USER_DAILY_DOCS` | `5` | Documents per user per UTC day |
| `GLOBAL_DAILY_DOCS` | `300` | Documents for all users per UTC day |
| `GLOBAL_DAILY_BUDGET_USD` | `0.50` | Daily LLM spend for all users (paid-tier list prices) |
| `QUEUE_MAX` | `10` | Uploads allowed to wait for processing; more are refused |
| `RETENTION_DAYS` | `7` | Days before extracted fields are deleted |
| `LOG_LEVEL` | `INFO` | Log level |
| `API_TOKEN` | required for the API | Bearer token for `POST /extract` (secret) |
| `API_HOST` | `127.0.0.1` | Address the HTTP API listens on |
| `API_PORT` | `8080` | Port the HTTP API listens on |
| `API_DAILY_DOCS` | `100` | Documents the HTTP API accepts per UTC day |
| `LLM_FAKE` | `0` | `1`: built-in synthetic answers instead of the model, for checks without keys |

## Author

Vladimir K — [Upwork profile](https://www.upwork.com/freelancers/~01944470ea8eacd678)

## License

MIT
