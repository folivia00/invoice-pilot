"""Memory benchmark of the bot process (Linux only: reads /proc/self/status).

One run = one fresh process, no network and no Telegram:

1. migrate a SQLite database in a temporary directory (Alembic in a subprocess,
   so the measured process never imports alembic, like the bot);
2. import ``invoice_pilot.bot``, build the services and the aiogram dispatcher
   exactly as ``bot.app`` does, with the real ``GeminiProvider`` whose httpx
   client uses a ``MockTransport`` that answers with recorded eval responses;
3. warm-up: one text PDF, one scan PDF, one photo from the dataset, then a CSV
   and an XLSX export; ``gc.collect()``; rest = VmRSS;
4. peaks: VmHWM is reset (``/proc/self/clear_refs`` = 5) before each case:
   a 5 MB scan PDF, a 5 MB photo, and 10 uploads waiting in the queue while a
   5 MB scan is processed (then all 11 are processed).

The processor runs ``gc.collect()`` after every document and logs the time as
``gc_ms``; the benchmark collects those values from the log records. The
allocator setting comes from the environment (``container.sh`` sets
``MALLOC_MMAP_THRESHOLD_``) and is reported back.

Uploads are "downloaded" the way the handler does it: bytes into a BytesIO,
then ``getvalue()``. Prints one JSON line with the values in MiB.

Usage (inside the container, see bench/README.md):
    python bench/memory.py --big-dir /big
"""

import argparse
import asyncio
import gc
import hashlib
import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DATASET = REPO / "eval" / "dataset"
RECORDINGS = REPO / "eval" / "recordings" / "gemini" / "gemini-3.5-flash-lite"
FAKE_TOKEN = "123456789:BENCH-not-a-real-token-0000000000"


def status_kib(field: str) -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(field + ":"):
            return int(line.split()[1])
    raise RuntimeError(f"{field} not found in /proc/self/status")


class GcTimes(logging.Handler):
    """Keeps ``gc_ms`` of every "document processed" log line."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.values: list[int] = []

    def emit(self, record: logging.LogRecord) -> None:
        match = re.search(r" gc_ms=(\d+)", record.getMessage())
        if match:
            self.values.append(int(match.group(1)))


def mib(kib: int) -> float:
    return round(kib / 1024, 1)


def reset_peak() -> None:
    Path("/proc/self/clear_refs").write_text("5")


def recorded_answers() -> dict[str, dict[str, Any]]:
    """Recordings keyed by document_sha256 (sha256 of the text layer or of the file)."""
    answers = {}
    for path in RECORDINGS.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        answers[payload["document_sha256"]] = payload
    return answers


def envelope(recording: dict[str, Any]) -> bytes:
    usage = recording["usage"]
    return json.dumps(
        {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": recording["raw_text"]}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": usage["input_tokens"],
                "candidatesTokenCount": usage["output_tokens"],
                "thoughtsTokenCount": 0,
                "totalTokenCount": usage["input_tokens"] + usage["output_tokens"],
            },
            "modelVersion": "bench",
        }
    ).encode("utf-8")


def migrate(data_dir: Path) -> None:
    env = {**os.environ, "DATA_DIR": str(data_dir)}
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO / "alembic.ini"), "upgrade", "head"],
        check=True,
        env=env,
        cwd=REPO,
        capture_output=True,
    )


async def run(big_dir: Path) -> dict[str, Any]:
    import httpx
    from aiogram import Bot

    from invoice_pilot.bot.app import build_dispatcher, build_services
    from invoice_pilot.config import load_settings
    from invoice_pilot.extraction.prepare import prepare
    from invoice_pilot.services.processing import UploadMeta
    from invoice_pilot.storage import Storage

    after_import = status_kib("VmRSS")
    gc_times = GcTimes()
    processing_log = logging.getLogger("invoice_pilot.services.processing")
    processing_log.addHandler(gc_times)
    processing_log.setLevel(logging.INFO)
    processing_log.propagate = False
    tmp = Path(tempfile.mkdtemp(prefix="bench-"))
    migrate(tmp)
    settings = load_settings(
        {
            "BOT_TOKEN": FAKE_TOKEN,
            "GEMINI_API_KEY": "bench-not-a-real-key",
            "PROFILE": "compact",
            "DATA_DIR": str(tmp),
            "USER_DAILY_DOCS": "1000",
            "GLOBAL_DAILY_DOCS": "1000",
            "GLOBAL_DAILY_BUDGET_USD": "100",
            "LLM_RPM": "10000",  # the limiter would only add waiting time here
            "QUEUE_MAX": "10",
        }
    )
    answers = recorded_answers()
    current: dict[str, bytes] = {}
    gate = asyncio.Event()
    gate.set()
    entered = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await gate.wait()
        return httpx.Response(
            200, content=current["body"], headers={"content-type": "application/json"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    storage = Storage.for_path(settings.database_path)
    services = build_services(settings, storage, client)
    dispatcher = build_dispatcher(settings, services)
    bot = Bot(settings.bot_token)
    processor = services.processor

    def answer_for(data: bytes) -> bytes:
        prepared = prepare(data)
        key = prepared.input.content_sha256()
        del prepared
        return envelope(answers.get(key) or answers[fallback_key])

    scan_ref = (DATASET / "docs" / "inv-003.pdf").read_bytes()
    fallback_key = hashlib.sha256(scan_ref).hexdigest()
    del scan_ref

    async def upload(path: Path, user: int, mime: str) -> str:
        size = path.stat().st_size

        async def download() -> bytes:
            buffer = io.BytesIO()
            buffer.write(path.read_bytes())
            data = buffer.getvalue()
            buffer.close()
            return data

        async def notify(text: str) -> None:
            pass

        meta = UploadMeta("document", mime, path.name, size)
        result = await processor.process(user, meta, download, notify)
        return result.kind.value

    def set_answer(path: Path) -> None:
        current["body"] = answer_for(path.read_bytes())

    results: dict[str, Any] = {
        "after_import_mib": mib(after_import),
        "malloc_mmap_threshold": os.environ.get("MALLOC_MMAP_THRESHOLD_"),
    }
    kinds = []
    for name, mime in (
        ("inv-001.pdf", "application/pdf"),
        ("inv-003.pdf", "application/pdf"),
        ("inv-005.jpg", "image/jpeg"),
    ):
        path = DATASET / "docs" / name
        set_answer(path)
        kinds.append(await upload(path, 1, mime))
    csv_file = await services.exports.export(1, "csv")
    xlsx_file = await services.exports.export(1, "xlsx")
    assert csv_file is not None and xlsx_file is not None
    del csv_file, xlsx_file
    gc.collect()
    results["warmup_results"] = kinds
    results["rest_rss_mib"] = mib(status_kib("VmRSS"))
    results["warmup_hwm_mib"] = mib(status_kib("VmHWM"))

    for case, name, mime in (
        ("scan", "scan_5mb.pdf", "application/pdf"),
        ("photo", "photo_5mb.jpg", "image/jpeg"),
    ):
        path = big_dir / name
        current["body"] = envelope(answers[fallback_key])
        gc.collect()
        reset_peak()
        start = status_kib("VmRSS")
        kind = await upload(path, 2, mime)
        results[f"{case}_result"] = kind
        results[f"{case}_start_rss_mib"] = mib(start)
        results[f"{case}_peak_hwm_mib"] = mib(status_kib("VmHWM"))
        gc.collect()
        results[f"{case}_after_rss_mib"] = mib(status_kib("VmRSS"))

    # 10 uploads wait while a 5 MB scan is at the (held) model call
    path = big_dir / "scan_5mb.pdf"
    current["body"] = envelope(answers[fallback_key])
    gc.collect()
    reset_peak()
    gate.clear()
    entered.clear()
    first = asyncio.create_task(upload(path, 100, "application/pdf"))
    await entered.wait()
    waiting = [asyncio.create_task(upload(path, 101 + n, "application/pdf")) for n in range(10)]
    for _ in range(200):
        if processor_waiting(services) == 10:
            break
        await asyncio.sleep(0.01)
    results["queue_waiting"] = processor_waiting(services)
    results["queue_waiting_rss_mib"] = mib(status_kib("VmRSS"))
    results["queue_waiting_hwm_mib"] = mib(status_kib("VmHWM"))
    gate.set()
    done = await asyncio.gather(first, *waiting)
    results["queue_results"] = sorted(set(done))
    results["queue_peak_hwm_mib"] = mib(status_kib("VmHWM"))
    gc.collect()
    results["final_rss_mib"] = mib(status_kib("VmRSS"))
    results["gc_ms"] = gc_times.values

    await client.aclose()
    await storage.dispose()
    await bot.session.close()
    del dispatcher
    results["modules_loaded"] = {
        m: m in sys.modules for m in ("aiogram", "pypdf", "openpyxl", "PIL", "alembic")
    }
    return results


def processor_waiting(services: Any) -> int:
    return int(services.processor.queue.waiting)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--big-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.big_dir)), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
