"""Generate the synthetic evaluation dataset.

Usage: uv run python eval/generate.py --seed 20261023 [--out eval/dataset]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evalgen.dataset import generate_dataset  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic invoice eval dataset.")
    parser.add_argument("--seed", type=int, default=20261023)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "dataset")
    args = parser.parse_args()
    started = time.perf_counter()
    manifest = generate_dataset(args.seed, args.out)
    elapsed = time.perf_counter() - started
    print(
        f"dataset v{manifest['dataset_version']}: {manifest['document_count']} documents "
        f"written to {args.out} in {elapsed:.1f}s (seed {args.seed})"
    )


if __name__ == "__main__":
    main()
