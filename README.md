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

## Author

Vladimir K — [Upwork profile](https://www.upwork.com/freelancers/~01944470ea8eacd678)

## License

MIT
