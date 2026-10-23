.PHONY: lint format test eval-generate

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .

test:
	uv run pytest

eval-generate:
	uv run python eval/generate.py --seed 20261023
