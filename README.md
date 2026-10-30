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

## Configuration

Settings are read from environment variables (see `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `BOT_TOKEN` | required | Telegram bot token (secret) |
| `GEMINI_API_KEY` | required | Gemini API key (secret) |
| `PROFILE` | `compact` | `compact`: 5 MB files, one document at a time; `full`: 10 MB, 4 in parallel; up to 5 pages in both |
| `DATA_DIR` | `./data` | Directory of the SQLite database |
| `ADMIN_IDS` | empty | Comma-separated Telegram user IDs with admin rights |
| `LLM_MODEL` | `gemini-3.5-flash-lite` | Gemini model (must have a configured price) |
| `LLM_RPM` | `14` | Maximum model requests per minute, retries included |
| `USER_DAILY_DOCS` | `5` | Documents per user per UTC day |
| `GLOBAL_DAILY_DOCS` | `300` | Documents for all users per UTC day |
| `GLOBAL_DAILY_BUDGET_USD` | `0.50` | Daily LLM spend for all users (paid-tier list prices) |
| `RETENTION_DAYS` | `7` | Days before extracted fields are deleted |
| `LOG_LEVEL` | `INFO` | Log level |

## Author

Vladimir K — [Upwork profile](https://www.upwork.com/freelancers/~01944470ea8eacd678)

## License

MIT
