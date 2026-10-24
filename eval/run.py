"""Evaluate model configurations on the dataset (replay by default).

Usage:
  uv run python eval/run.py                         # replay every configuration with recordings
  uv run python eval/run.py --config gemini-flash-lite --ids inv-001,inv-002
  uv run python eval/run.py --mode record --live --config gemini-flash-lite \
      --env-file ../secrets/.env --max-cost 0.50 --limit 5
Exit codes: 0 ok, 1 incomplete (missing recordings or stopped by --max-cost), 2 refused.
"""

import argparse
import asyncio
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evalrun.configs import CONFIGS  # noqa: E402
from evalrun.runner import RECORDINGS_DIR, RESULTS_DIR, RunOptions, run_eval  # noqa: E402


def _money(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"not a USD amount: {text!r}") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mode", choices=("replay", "record"), default="replay")
    parser.add_argument("--live", action="store_true", help="confirm live API calls (record)")
    parser.add_argument(
        "--config", action="append", default=[], choices=sorted(CONFIGS), help="repeatable"
    )
    parser.add_argument("--ids", help="comma-separated document ids, e.g. inv-001,inv-002")
    parser.add_argument("--limit", type=int, help="first N documents, balanced over input types")
    parser.add_argument("--env-file", type=Path, help="KEY=VALUE file with the API keys (record)")
    parser.add_argument("--max-cost", type=_money, help="USD cap for a live run (record)")
    parser.add_argument("--concurrency", type=int, default=2, help="parallel live requests")
    parser.add_argument("--recordings-dir", type=Path, default=RECORDINGS_DIR)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = RunOptions(
        configs=args.config,
        mode=args.mode,
        live=args.live,
        ids=[i.strip() for i in args.ids.split(",") if i.strip()] if args.ids else None,
        limit=args.limit,
        env_file=args.env_file,
        max_cost=args.max_cost,
        concurrency=args.concurrency,
        recordings_dir=args.recordings_dir,
        results_dir=args.results_dir,
    )
    result = asyncio.run(run_eval(options))
    for line in result.messages:
        print(line)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
