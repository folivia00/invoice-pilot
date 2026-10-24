.PHONY: lint format test eval-generate eval cost-report

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .

test:
	uv run pytest

eval-generate:
	uv run python eval/generate.py --seed 20261023

eval:
	uv run python eval/run.py --mode replay

cost-report:
	uv run python eval/cost_report.py
