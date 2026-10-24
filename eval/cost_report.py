"""Cost report from recorded answers: eval/results/cost.md.

Usage: uv run python eval/cost_report.py [--config NAME ...] [--out eval/results/cost.md]
Without --config every configuration that has recordings is included. No network.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evalrun.configs import CONFIGS, EvalConfig  # noqa: E402
from evalrun.cost import RecordedUsage, load_recorded_usage, render_cost_report  # noqa: E402
from evalrun.runner import (  # noqa: E402
    DATASET_DIR,
    RECORDINGS_DIR,
    RESULTS_DIR,
    Dataset,
    configs_with_recordings,
    document_inputs,
    load_dataset,
    replay_inner,
)
from invoice_pilot.llm.recording import RecordingProvider  # noqa: E402
from invoice_pilot.llm.types import DocumentInput  # noqa: E402


def collect_usage(
    config: EvalConfig,
    dataset: Dataset,
    inputs: dict[str, DocumentInput],
    recordings_dir: Path,
) -> list[RecordedUsage]:
    wrapper = RecordingProvider(replay_inner(config.llm), mode="replay", root=recordings_dir)
    usages = []
    for gt in dataset.ground_truths:
        path = wrapper.path(inputs[gt["id"]])
        if path.is_file():
            usages.append(load_recorded_usage(path, gt["id"], gt["input_type"], wrapper.model))
    return usages


def build_report(
    names: list[str], dataset_dir: Path = DATASET_DIR, recordings_dir: Path = RECORDINGS_DIR
) -> str:
    dataset = load_dataset(dataset_dir)
    inputs = document_inputs(dataset, dataset_dir)
    if not names:
        names = configs_with_recordings(inputs, recordings_dir)
    sections = [
        (CONFIGS[name], collect_usage(CONFIGS[name], dataset, inputs, recordings_dir))
        for name in names
    ]
    return render_cost_report(sections)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cost report from recorded model answers.")
    parser.add_argument("--config", action="append", default=[], choices=sorted(CONFIGS))
    parser.add_argument("--recordings-dir", type=Path, default=RECORDINGS_DIR)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "cost.md")
    args = parser.parse_args(argv)
    report = build_report(args.config, recordings_dir=args.recordings_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8", newline="\n")
    print(f"cost report written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
