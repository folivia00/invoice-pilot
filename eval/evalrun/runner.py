"""Run one or more configurations over the dataset and write the results.

Replay (default) needs no key and never touches the network: every document is
served from ``eval/recordings``; a missing recording is reported and the exit
code is 1. Record mode requires ``--live``, an env file with the key and
``--max-cost``; a worst-case estimate is checked before the first call and the
actual cost after every document. A provider spend limit stops the run at once.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Literal

import httpx

from evalrun import METHODOLOGY_VERSION
from evalrun.compare import all_key_fields_correct, compare_invoices
from evalrun.configs import CONFIGS, EvalConfig, get_config
from evalrun.cost import estimate_cost
from evalrun.envfile import EnvFileError, parse_env_file, require_key
from evalrun.metrics import compute_metrics
from evalrun.report import render_summary
from invoice_pilot.domain import Invoice
from invoice_pilot.extraction.prepare import PreparedDocument, prepare
from invoice_pilot.llm.config import LLMConfig
from invoice_pilot.llm.errors import (
    ProviderError,
    ProviderUnavailableError,
    RecordingMissingError,
    SpendLimitError,
)
from invoice_pilot.llm.pricing import cost_usd
from invoice_pilot.llm.prompts import PROMPT_VERSION
from invoice_pilot.llm.providers import Provider, make_provider
from invoice_pilot.llm.recording import RecordingProvider
from invoice_pilot.llm.schema import schema_sha256
from invoice_pilot.llm.types import DocumentInput, UsageRecord
from invoice_pilot.services.extraction import extract_invoice

EVAL_DIR: Final = Path(__file__).resolve().parent.parent
DATASET_DIR: Final = EVAL_DIR / "dataset"
RECORDINGS_DIR: Final = EVAL_DIR / "recordings"
RESULTS_DIR: Final = EVAL_DIR / "results"

Mode = Literal["replay", "record"]
ProviderFactory = Callable[..., Provider]
Clock = Callable[[], datetime]

EXIT_OK: Final = 0
EXIT_INCOMPLETE: Final = 1  # missing recordings, cost cap or provider spend limit
EXIT_REFUSED: Final = 2  # nothing was run

STOP_MAX_COST: Final = "max_cost"
STOP_SPEND_LIMIT: Final = "provider_spend_limit"


@dataclass(slots=True)
class RunOptions:
    configs: list[str]
    mode: Mode = "replay"
    live: bool = False
    ids: list[str] | None = None
    limit: int | None = None
    env_file: Path | None = None
    max_cost: Decimal | None = None
    concurrency: int = 2
    skip_recorded: bool = False
    dataset_dir: Path = DATASET_DIR
    recordings_dir: Path = RECORDINGS_DIR
    results_dir: Path = RESULTS_DIR


@dataclass(slots=True)
class ConfigResult:
    name: str
    processed: int
    selected: int
    skipped: int
    missing: list[str]
    stopped: str | None
    stop_detail: str | None
    cost_usd: Decimal
    metrics: dict[str, Any]


@dataclass(slots=True)
class RunResult:
    exit_code: int
    messages: list[str] = field(default_factory=list)
    configs: dict[str, ConfigResult] = field(default_factory=dict)


class RunRefused(Exception):
    """Nothing was executed; the message explains why (never contains secrets)."""


# --- dataset ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Dataset:
    version: str
    reference_date: date
    ground_truths: list[dict[str, Any]]  # sorted by id

    @property
    def ids(self) -> list[str]:
        return [gt["id"] for gt in self.ground_truths]


def load_dataset(dataset_dir: Path) -> Dataset:
    manifest = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    truths = [
        json.loads((dataset_dir / "ground_truth" / f"{doc['id']}.json").read_text(encoding="utf-8"))
        for doc in manifest["documents"]
    ]
    truths.sort(key=lambda gt: gt["id"])
    return Dataset(
        version=manifest["dataset_version"],
        reference_date=date.fromisoformat(manifest["reference_date"]),
        ground_truths=truths,
    )


def select_documents(
    dataset: Dataset, ids: list[str] | None, limit: int | None
) -> list[dict[str, Any]]:
    """Documents in id order; ``limit`` keeps the first N round-robin over input types.

    A pilot of N documents thus covers text PDFs, scans and photos alike, which is
    what the cost estimate needs (token counts differ by input type).
    """
    truths = dataset.ground_truths
    if ids is not None:
        known = {gt["id"]: gt for gt in truths}
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise RunRefused(f"unknown document ids: {', '.join(unknown)}")
        truths = [known[i] for i in sorted(set(ids))]
    if limit is None or limit >= len(truths):
        return truths
    queues: dict[str, list[dict[str, Any]]] = {}
    for gt in truths:
        queues.setdefault(gt["input_type"], []).append(gt)
    chosen: list[dict[str, Any]] = []
    while len(chosen) < limit:
        progressed = False
        for kind in ("text_pdf", "scan_pdf", "photo"):
            if queues.get(kind) and len(chosen) < limit:
                chosen.append(queues[kind].pop(0))
                progressed = True
        if not progressed:
            break
    chosen.sort(key=lambda gt: gt["id"])
    return chosen


@dataclass(frozen=True, slots=True)
class LoadedDocument:
    gt: dict[str, Any]
    data: bytes
    prepared: PreparedDocument

    @property
    def id(self) -> str:
        return self.gt["id"]


def load_documents(docs: list[dict[str, Any]], dataset_dir: Path) -> list[LoadedDocument]:
    """Read and prepare the selected documents once (shared by key lookup and extraction)."""
    loaded = []
    for gt in docs:
        data = (dataset_dir / gt["file"]).read_bytes()
        loaded.append(LoadedDocument(gt=gt, data=data, prepared=prepare(data)))
    return loaded


def document_inputs(dataset: Dataset, dataset_dir: Path) -> dict[str, DocumentInput]:
    """Provider input of every dataset document (needed to compute recording keys)."""
    return {
        gt["id"]: prepare((dataset_dir / gt["file"]).read_bytes()).input
        for gt in dataset.ground_truths
    }


# --- providers -------------------------------------------------------------------------


def _exploding_client() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("replay mode must not send requests")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def replay_inner(config: LLMConfig) -> Provider:
    """A real provider object (for identical recording keys) that can never be called."""
    return make_provider(config, api_key="replay-no-key", client=_exploding_client())


def configs_with_recordings(inputs: dict[str, DocumentInput], recordings_dir: Path) -> list[str]:
    """Names of configurations that have at least one recording for the dataset."""
    names = []
    for name, config in CONFIGS.items():
        wrapper = RecordingProvider(replay_inner(config.llm), mode="replay", root=recordings_dir)
        if not (recordings_dir / wrapper.name / wrapper.model).is_dir():
            continue
        if any(wrapper.path(doc).is_file() for doc in inputs.values()):
            names.append(name)
    return names


# --- one document ----------------------------------------------------------------------


def _usage_dict(record: UsageRecord) -> dict[str, Any]:
    return {
        "status": record.status,
        "http_status": record.http_status,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "thinking_tokens": record.thinking_tokens,
        "latency_ms": record.latency_ms,
    }


def _base_prediction(gt: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": gt["id"],
        "input_type": gt["input_type"],
        "language": gt["language"],
        "template": gt["template"],
        "corruption": gt["corruption"],
        "expected_issue_codes": list(gt["expected_issue_codes"]),
        "status": None,
        "issue_codes": [],
        "fields": compare_invoices(Invoice.model_validate(gt["invoice"]), None),
        "key_fields_correct": False,
        "attempts": 0,
        "usage": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "cost_usd": "0",
        "recorded_at": None,
        "error": None,
        "spend_limit_code": None,
    }


async def process_document(
    doc: LoadedDocument,
    wrapper: RecordingProvider,
    reference_date: date,
    price_date: Callable[[str | None], date],
) -> dict[str, Any]:
    """Run the pipeline for one document and return its prediction row."""
    pred = _base_prediction(doc.gt)
    expected = Invoice.model_validate(doc.gt["invoice"])
    recording_path = wrapper.path(doc.prepared.input)
    try:
        outcome = await extract_invoice(
            doc.data, provider=wrapper, today=reference_date, prepared=doc.prepared
        )
    except RecordingMissingError:
        pred["status"] = "missing_recording"
        return pred
    except (ProviderUnavailableError, ProviderError) as exc:
        pred["status"] = "provider_error"
        pred["error"] = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, SpendLimitError):
            pred["spend_limit_code"] = exc.code
        records = list(exc.attempts)
    else:
        pred["status"] = outcome.report.status.value
        pred["issue_codes"] = sorted({issue.code.value for issue in outcome.report.issues})
        pred["fields"] = compare_invoices(expected, outcome.invoice)
        pred["key_fields_correct"] = all_key_fields_correct(pred["fields"])
        pred["attempts"] = outcome.attempts
        records = outcome.usage
    if recording_path.is_file():
        payload = json.loads(recording_path.read_text(encoding="utf-8"))
        pred["recorded_at"] = payload.get("recorded_at")
    day = price_date(pred["recorded_at"])
    pred["usage"] = [_usage_dict(r) for r in records]
    pred["input_tokens"] = sum(r.input_tokens for r in records)
    pred["output_tokens"] = sum(r.output_tokens for r in records)
    pred["thinking_tokens"] = sum(r.thinking_tokens for r in records)
    pred["cost_usd"] = str(sum((cost_usd(r, day) for r in records), Decimal(0)))
    return pred


def _skipped_prediction(gt: dict[str, Any]) -> dict[str, Any]:
    pred = _base_prediction(gt)
    pred["status"] = "skipped_recorded"
    return pred


# --- one configuration -----------------------------------------------------------------


def _price_date_for(clock: Clock) -> Callable[[str | None], date]:
    def price_date(recorded_at: str | None) -> date:
        if recorded_at:
            return date.fromisoformat(recorded_at[:10])
        return clock().date()

    return price_date


@dataclass(slots=True)
class Prepared:
    """A configuration ready to run: its wrapper, the documents it will request, its estimate."""

    config: EvalConfig
    wrapper: RecordingProvider
    skipped: set[str]
    estimate: Decimal


def _build_wrapper(
    config: EvalConfig,
    options: RunOptions,
    provider_factory: ProviderFactory,
    clock: Clock,
    clients: list[httpx.AsyncClient],
) -> RecordingProvider:
    if options.mode == "replay":
        inner = replay_inner(config.llm)
        return RecordingProvider(inner, mode="replay", root=options.recordings_dir, clock=clock)
    assert options.env_file is not None
    values = parse_env_file(options.env_file)
    api_key = require_key(values, config.key_env, options.env_file)
    client = httpx.AsyncClient()
    clients.append(client)
    inner = provider_factory(config.llm, api_key=api_key, client=client)
    return RecordingProvider(inner, mode="record", root=options.recordings_dir, clock=clock)


async def run_config(
    prepared: Prepared,
    dataset: Dataset,
    docs: list[LoadedDocument],
    options: RunOptions,
    clock: Clock,
) -> ConfigResult:
    config, wrapper = prepared.config, prepared.wrapper
    price_date = _price_date_for(clock)
    concurrency = options.concurrency if options.mode == "record" else 1
    semaphore = asyncio.Semaphore(max(1, concurrency))
    spent = Decimal(0)
    stopped: str | None = None
    stop_detail: str | None = None
    results: list[dict[str, Any] | None] = [None] * len(docs)

    async def worker(index: int, doc: LoadedDocument) -> None:
        nonlocal spent, stopped, stop_detail
        if doc.id in prepared.skipped:
            results[index] = _skipped_prediction(doc.gt)
            return
        async with semaphore:
            if stopped:
                return
            pred = await process_document(doc, wrapper, dataset.reference_date, price_date)
            results[index] = pred
            spent += Decimal(pred["cost_usd"])
            if pred["spend_limit_code"]:
                stopped, stop_detail = STOP_SPEND_LIMIT, pred["spend_limit_code"]
            elif options.max_cost is not None and spent > options.max_cost:
                stopped = STOP_MAX_COST

    await asyncio.gather(*(worker(i, doc) for i, doc in enumerate(docs)))
    preds = [p for p in results if p is not None]
    metrics = compute_metrics(preds)
    metrics["skipped_recorded"] = len(prepared.skipped)
    metrics["run"] = _run_header(config, wrapper, dataset, docs, preds, options, stopped)
    _write_results(options.results_dir / config.name, preds, metrics)
    return ConfigResult(
        name=config.name,
        processed=len(preds) - len(prepared.skipped),
        selected=len(docs),
        skipped=len(prepared.skipped),
        missing=metrics["missing_recordings"],
        stopped=stopped,
        stop_detail=stop_detail,
        cost_usd=spent,
        metrics=metrics,
    )


def _run_header(
    config: EvalConfig,
    wrapper: RecordingProvider,
    dataset: Dataset,
    docs: list[LoadedDocument],
    preds: list[dict[str, Any]],
    options: RunOptions,
    stopped: str | None,
) -> dict[str, Any]:
    dates = sorted(p["recorded_at"] for p in preds if p["recorded_at"])
    return {
        "config": config.name,
        "provider": wrapper.name,
        "model": wrapper.model,
        "params": dict(wrapper.params),
        "mode": options.mode,
        "prompt_version": PROMPT_VERSION,
        "schema_sha256": schema_sha256(),
        "dataset_version": dataset.version,
        "reference_date": dataset.reference_date.isoformat(),
        "methodology_version": METHODOLOGY_VERSION,
        "documents_selected": len(docs),
        "documents_processed": len(preds),
        "recordings_first": dates[0] if dates else None,
        "recordings_last": dates[-1] if dates else None,
        "run_date": dates[-1][:10] if dates else None,
        "stopped": stopped,
    }


def _write_results(out_dir: Path, preds: list[dict[str, Any]], metrics: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(p, sort_keys=True, ensure_ascii=False) for p in preds]
    (out_dir / "predictions.jsonl").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n"
    )
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (out_dir / "summary.md").write_text(render_summary(metrics), encoding="utf-8", newline="\n")


# --- whole run -------------------------------------------------------------------------


def _check_options(options: RunOptions, configs: list[EvalConfig]) -> None:
    if options.mode == "record":
        if not options.live:
            raise RunRefused("record mode makes live API calls: pass --live to confirm")
        if options.env_file is None:
            raise RunRefused("record mode needs --env-file with the API keys")
        if options.max_cost is None or options.max_cost <= 0:
            raise RunRefused("record mode needs --max-cost USD (positive)")
        values = parse_env_file(options.env_file)
        for config in configs:
            require_key(values, config.key_env, options.env_file)


def _resolve_configs(options: RunOptions, dataset: Dataset) -> list[EvalConfig]:
    if options.configs:
        return [get_config(name) for name in options.configs]
    if options.mode == "record":
        raise RunRefused("record mode needs explicit --config")
    inputs = document_inputs(dataset, options.dataset_dir)
    names = configs_with_recordings(inputs, options.recordings_dir)
    if not names:
        raise RunRefused(f"no recordings found under {options.recordings_dir}")
    return [CONFIGS[name] for name in names]


def _plan_config(
    config: EvalConfig, docs: list[LoadedDocument], options: RunOptions, clock: Clock
) -> tuple[set[str], Decimal]:
    """Documents to skip (already recorded) and the worst-case estimate for the rest.

    Uses a key-only wrapper, so no provider with a real key exists until the
    estimate has passed.
    """
    lookup = RecordingProvider(replay_inner(config.llm), mode="replay", root=options.recordings_dir)
    skipped: set[str] = set()
    if options.mode == "record" and options.skip_recorded:
        skipped = {doc.id for doc in docs if lookup.path(doc.prepared.input).is_file()}
    requested = [doc.gt["input_type"] for doc in docs if doc.id not in skipped]
    estimate = Decimal(0)
    if options.mode == "record":
        estimate = estimate_cost(config, requested, clock().date())
    return skipped, estimate


async def run_eval(
    options: RunOptions,
    *,
    provider_factory: ProviderFactory = make_provider,
    clock: Clock = lambda: datetime.now(UTC),
) -> RunResult:
    """Run every selected configuration; never raises for expected failures."""
    result = RunResult(exit_code=EXIT_OK)
    clients: list[httpx.AsyncClient] = []
    try:
        try:
            dataset = load_dataset(options.dataset_dir)
            configs = _resolve_configs(options, dataset)
            _check_options(options, configs)
            selected = select_documents(dataset, options.ids, options.limit)
            docs = load_documents(selected, options.dataset_dir)
            plans = [(config, *_plan_config(config, docs, options, clock)) for config in configs]
            for config, skipped, estimate in plans:
                requested = len(docs) - len(skipped)
                if options.mode == "record":
                    assert options.max_cost is not None
                    if estimate > options.max_cost:
                        raise RunRefused(
                            f"{config.name}: worst-case estimate ${estimate:.4f} for "
                            f"{requested} documents exceeds --max-cost ${options.max_cost}"
                        )
                    result.messages.append(
                        f"{config.name}: worst-case estimate ${estimate:.4f} for "
                        f"{requested} documents ({len(skipped)} already recorded, skipped)"
                    )
            prepared = [
                Prepared(
                    config=config,
                    wrapper=_build_wrapper(config, options, provider_factory, clock, clients),
                    skipped=skipped,
                    estimate=estimate,
                )
                for config, skipped, estimate in plans
            ]
        except (RunRefused, EnvFileError, ValueError, OSError) as exc:
            result.exit_code = EXIT_REFUSED
            result.messages.append(f"refused: {exc}")
            return result

        for item in prepared:
            config_result = await run_config(item, dataset, docs, options, clock)
            result.configs[item.config.name] = config_result
            _report_config(result, config_result)
    finally:
        for client in clients:
            await client.aclose()
    return result


def _report_config(result: RunResult, config_result: ConfigResult) -> None:
    name = config_result.name
    overall = config_result.metrics["overall"]
    result.messages.append(
        f"{name}: {config_result.processed}/{config_result.selected} documents"
        f"{f' ({config_result.skipped} skipped)' if config_result.skipped else ''}, "
        f"precision among auto-approved {_pct(overall['precision_auto_approved'])}, "
        f"cost ${config_result.cost_usd:.4f}"
    )
    if config_result.missing:
        result.exit_code = EXIT_INCOMPLETE
        result.messages.append(
            f"{name}: {len(config_result.missing)} missing recordings: "
            + ", ".join(config_result.missing)
        )
    if config_result.stopped == STOP_SPEND_LIMIT:
        result.exit_code = EXIT_INCOMPLETE
        result.messages.append(
            f"{name}: provider spend limit reached ({config_result.stop_detail}); stopped after "
            f"{config_result.processed} documents, partial results written; add credits or "
            "raise the limit before running again"
        )
    elif config_result.stopped:
        result.exit_code = EXIT_INCOMPLETE
        result.messages.append(
            f"{name}: stopped after {config_result.processed} documents "
            f"({config_result.stopped}); partial results written"
        )


def _pct(item: dict[str, Any]) -> str:
    if item["value"] is None:
        return "n/a"
    return f"{item['value'] * 100:.1f} % ({item['numerator']}/{item['denominator']})"
