"""HTTP API: auth, upload checks, limits, errors, logs, startup; the fake model only."""

import asyncio
import logging
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from aiohttp import BodyPartReader, FormData
from aiohttp.test_utils import TestClient, TestServer
from helpers_app import REPO, FakeClock, dataset_bytes, ground_truth, open_storage

from invoice_pilot.api import __main__ as api_main
from invoice_pilot.api.app import (
    API_USER_ID,
    CHUNK_BYTES,
    MULTIPART_OVERHEAD_BYTES,
    build_app,
)
from invoice_pilot.api.server import run
from invoice_pilot.config import ConfigError, Settings, load_settings
from invoice_pilot.extraction.prepare import InputLimits
from invoice_pilot.llm.errors import ProviderUnavailableError
from invoice_pilot.llm.types import DocumentInput, ProviderResponse, UsageRecord
from invoice_pilot.logging_setup import QUIET_LOGGERS, apply_quiet_levels
from invoice_pilot.services.fake_llm import FAKE_MODEL, FakeProvider, zero_cost
from invoice_pilot.services.limits import DailyLimits
from invoice_pilot.services.processing import DocumentProcessor, ProcessingQueue
from invoice_pilot.services.rate_limit import RateLimitedProvider
from invoice_pilot.services.wiring import build_provider

TOKEN = "test-api-token-SECRET"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MB = 1024 * 1024

Scenario = Callable[[TestClient[Any, Any]], Awaitable[None]]


class UnavailableProvider:
    name = "gemini"
    model = FAKE_MODEL
    params: dict[str, str] = {}

    async def extract(self, doc: DocumentInput) -> ProviderResponse:
        record = UsageRecord("gemini", FAKE_MODEL, "provider_error", http_status=503)
        raise ProviderUnavailableError("model API unavailable", (record,))


class GatedProvider(FakeProvider):
    """The fake model, held on ``gate`` until the test releases it."""

    def __init__(self, gate: asyncio.Event) -> None:
        self.gate = gate
        self.started = asyncio.Event()

    async def extract(self, doc: DocumentInput) -> ProviderResponse:
        self.started.set()
        await self.gate.wait()
        return await super().extract(doc)


def processor(
    storage: Any,
    provider: Any = None,
    *,
    daily: int = 100,
    max_bytes: int = 10 * MB,
    max_pages: int = 5,
    queue_max: int = 10,
) -> DocumentProcessor:
    clock = FakeClock()
    limits = DailyLimits(
        storage,
        user_daily_docs=daily,
        global_daily_docs=300,
        global_daily_budget_usd=Decimal("0.50"),
        clock=clock,
    )
    return DocumentProcessor(
        storage=storage,
        limits=limits,
        provider=provider or FakeProvider(),
        queue=ProcessingQueue(1, queue_max),
        input_limits=InputLimits(max_bytes=max_bytes, max_pages=max_pages),
        clock=clock,
        monotonic=clock.monotonic,
        pricing=zero_cost,
    )


def run_api(db_path: Path, scenario: Scenario, max_bytes: int = 10 * MB, **options: Any) -> None:
    async def main() -> None:
        async with open_storage(db_path) as storage:
            app = build_app(
                processor(storage, max_bytes=max_bytes, **options),
                api_token=TOKEN,
                max_file_bytes=max_bytes,
            )
            async with TestClient(TestServer(app)) as client:
                await scenario(client)

    asyncio.run(main())


def form(data: bytes, name: str = "invoice.pdf", content_type: str = "application/pdf") -> FormData:
    body = FormData()
    body.add_field("file", data, filename=name, content_type=content_type)
    return body


# --- health and auth ---------------------------------------------------------------------


def test_health_needs_no_token_and_shows_no_configuration(db_path: Path) -> None:
    async def scenario(client: TestClient[Any, Any]) -> None:
        response = await client.get("/health")
        assert response.status == 200
        assert await response.json() == {"status": "ok"}

    run_api(db_path, scenario)


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}, {"Authorization": "Bearer "}],
)
def test_extract_needs_the_bearer_token(db_path: Path, headers: dict[str, str]) -> None:
    async def scenario(client: TestClient[Any, Any]) -> None:
        response = await client.post(
            "/extract", data=form(dataset_bytes("inv-001")), headers=headers
        )
        assert response.status == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert (await response.json())["error"]["code"] == "unauthorized"

    run_api(db_path, scenario)


# --- successful extraction -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("doc_id", "input_kind"),
    [("inv-001", "text_pdf"), ("inv-003", "scan_pdf"), ("inv-005", "photo")],
)
def test_extract_returns_the_validated_invoice(db_path: Path, doc_id: str, input_kind: str) -> None:
    """The declared Content-Type does not matter: the file signature decides."""

    async def scenario(client: TestClient[Any, Any]) -> None:
        response = await client.post(
            "/extract",
            data=form(dataset_bytes(doc_id), content_type="application/octet-stream"),
            headers=AUTH,
        )
        assert response.status == 200
        body = await response.json()
        assert set(body) == {
            "document_id",
            "status",
            "issues",
            "invoice",
            "input_kind",
            "pages",
            "model",
            "processing_ms",
        }
        assert body["status"] == "auto_approved"
        assert body["issues"] == []
        assert body["input_kind"] == input_kind
        assert body["pages"] == ground_truth(doc_id)["pages"]
        assert body["model"] == FAKE_MODEL
        assert isinstance(body["document_id"], int)
        invoice = body["invoice"]
        assert invoice["total"] == "3798.41"  # amounts are Decimal strings, never floats
        assert invoice["line_items"][2]["quantity"] == "5.7"
        assert invoice["invoice_date"] == "2025-11-27"

    run_api(db_path, scenario)


def test_api_documents_belong_to_the_api_user(db_path: Path) -> None:
    async def scenario(client: TestClient[Any, Any]) -> None:
        response = await client.post("/extract", data=form(dataset_bytes("inv-001")), headers=AUTH)
        assert response.status == 200

    run_api(db_path, scenario)

    async def check() -> None:
        async with open_storage(db_path) as storage, storage.transaction() as repo:
            assert (
                await repo.count_documents(
                    since=FakeClock().now.replace(hour=0),
                    statuses=("auto_approved",),
                    user=API_USER_ID,
                )
                == 1
            )

    asyncio.run(check())


# --- rejected uploads ----------------------------------------------------------------------


def test_unsupported_and_missing_files_are_400(db_path: Path) -> None:
    async def scenario(client: TestClient[Any, Any]) -> None:
        cases: list[tuple[Any, str]] = [
            (form(b"plain text, not an invoice", "notes.txt", "text/plain"), "unsupported_file"),
            (form(b"", "empty.pdf"), "missing_file"),
            ({"other": "value"}, "missing_file"),
        ]
        for data, code in cases:
            response = await client.post("/extract", data=data, headers=AUTH)
            assert response.status == 400
            assert (await response.json())["error"]["code"] == code
        response = await client.post(
            "/extract", data=b"%PDF-1.4", headers={**AUTH, "Content-Type": "application/pdf"}
        )
        assert response.status == 400
        assert (await response.json())["error"]["code"] == "missing_file"

    run_api(db_path, scenario)


def test_broken_pdf_is_400_invalid_file(db_path: Path) -> None:
    async def scenario(client: TestClient[Any, Any]) -> None:
        response = await client.post("/extract", data=form(b"%PDF-1.7\nbroken"), headers=AUTH)
        assert response.status == 400
        error = (await response.json())["error"]
        assert error == {
            "code": "invalid_file",
            "message": "Cannot process this file: the PDF cannot be read.",
        }

    run_api(db_path, scenario)


def test_pdf_over_the_page_limit_is_400(db_path: Path) -> None:
    async def scenario(client: TestClient[Any, Any]) -> None:
        response = await client.post("/extract", data=form(dataset_bytes("inv-001")), headers=AUTH)
        assert response.status == 400
        error = (await response.json())["error"]
        assert error["code"] == "invalid_file" and "limit is 0" in error["message"]

    run_api(db_path, scenario, max_pages=0)


def test_declared_body_over_the_limit_is_413_without_reading(db_path: Path) -> None:
    limit = 100_000

    async def scenario(client: TestClient[Any, Any]) -> None:
        big = form(b"%PDF-1.7\n" + b"0" * (limit + MULTIPART_OVERHEAD_BYTES))
        response = await client.post("/extract", data=big, headers=AUTH)
        assert response.status == 413
        assert (await response.json())["error"]["code"] == "file_too_large"

    run_api(db_path, scenario, max_bytes=limit)


def test_streamed_file_over_the_limit_stops_reading_at_the_limit(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    limit = 300_000
    total = 3 * MB
    read = [0]
    original = BodyPartReader.read_chunk

    async def counting(self: BodyPartReader, size: int = CHUNK_BYTES) -> bytes:
        chunk = await original(self, size)
        read[0] += len(chunk)
        return chunk

    monkeypatch.setattr(BodyPartReader, "read_chunk", counting)

    async def chunks() -> AsyncIterator[bytes]:
        yield b"%PDF-1.7\n"
        for _ in range(total // CHUNK_BYTES):
            yield b"0" * CHUNK_BYTES

    async def scenario(client: TestClient[Any, Any]) -> None:
        body = FormData()
        body.add_field("file", chunks(), filename="big.pdf", content_type="application/pdf")
        response = await client.post("/extract", data=body, headers=AUTH)  # chunked, no length
        assert response.status == 413
        assert read[0] <= limit + CHUNK_BYTES < total

    run_api(db_path, scenario, max_bytes=limit)


def test_client_max_size_matches_the_profile_limit(db_path: Path) -> None:
    async def main() -> None:
        async with open_storage(db_path) as storage:
            app = build_app(processor(storage), api_token=TOKEN, max_file_bytes=5 * MB)
            assert app._client_max_size == 5 * MB + MULTIPART_OVERHEAD_BYTES

    asyncio.run(main())


# --- limits and provider failures ----------------------------------------------------------


def test_daily_limit_is_429(db_path: Path) -> None:
    async def scenario(client: TestClient[Any, Any]) -> None:
        first = await client.post("/extract", data=form(dataset_bytes("inv-001")), headers=AUTH)
        second = await client.post("/extract", data=form(dataset_bytes("inv-001")), headers=AUTH)
        assert first.status == 200
        assert second.status == 429
        assert (await second.json())["error"]["code"] == "daily_limit"

    run_api(db_path, scenario, daily=1)


def test_provider_unavailable_is_503_with_retry_after(db_path: Path) -> None:
    async def scenario(client: TestClient[Any, Any]) -> None:
        response = await client.post("/extract", data=form(dataset_bytes("inv-001")), headers=AUTH)
        assert response.status == 503
        assert response.headers["Retry-After"] == "60"
        assert (await response.json())["error"]["code"] == "provider_unavailable"
        # the slot is given back: the next document is accepted again
        again = await client.post("/extract", data=form(dataset_bytes("inv-001")), headers=AUTH)
        assert again.status == 503

    run_api(db_path, scenario, provider=UnavailableProvider(), daily=1)


def test_full_queue_is_503_with_retry_after(db_path: Path) -> None:
    gate = asyncio.Event()

    async def main() -> None:
        provider = GatedProvider(gate)
        async with open_storage(db_path) as storage:
            app = build_app(
                processor(storage, provider, queue_max=0), api_token=TOKEN, max_file_bytes=10 * MB
            )
            async with TestClient(TestServer(app)) as client:
                first = asyncio.create_task(
                    client.post("/extract", data=form(dataset_bytes("inv-001")), headers=AUTH)
                )
                await provider.started.wait()
                busy = await client.post(
                    "/extract", data=form(dataset_bytes("inv-001")), headers=AUTH
                )
                assert busy.status == 503
                assert busy.headers["Retry-After"] == "60"
                assert (await busy.json())["error"]["code"] == "queue_full"
                gate.set()
                assert (await first).status == 200

    asyncio.run(main())


# --- logs ----------------------------------------------------------------------------------


def test_one_log_line_per_request_without_token_content_or_address(
    db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario(client: TestClient[Any, Any]) -> None:
        await client.post("/extract", data=form(dataset_bytes("inv-001")), headers=AUTH)
        await client.post("/extract", data=form(dataset_bytes("inv-001")))
        await client.post("/extract", data=form(b"text", "a.txt", "text/plain"), headers=AUTH)

    caplog.set_level(logging.INFO)
    saved = {name: logging.getLogger(name).level for name in QUIET_LOGGERS}
    apply_quiet_levels()  # as configure_logging does in the process
    try:
        run_api(db_path, scenario)
    finally:  # restore, not NOTSET: SQLAlchemy sets its own level at import
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)
    lines = [r.getMessage() for r in caplog.records if r.name == "invoice_pilot.api.app"]
    assert len(lines) == 3
    assert lines[0].startswith(
        "api extract: http_status=200 input_kind=text_pdf status=auto_approved latency_ms="
    )
    assert lines[0].endswith("cost_usd=0")
    assert lines[1].startswith("api extract: http_status=401 input_kind=- status=-")
    assert lines[2].startswith("api extract: http_status=400 input_kind=- status=-")
    for forbidden in (TOKEN, "SECRET", "Northwind", "3798.41", "127.0.0.1", "invoice.pdf"):
        assert forbidden not in caplog.text


# --- startup -------------------------------------------------------------------------------


def _api_env(**extra: str) -> dict[str, str]:
    return {"API_TOKEN": TOKEN, "GEMINI_API_KEY": "fake-gemini-key", **extra}


@pytest.mark.parametrize("value", [None, "", "   "])
def test_api_does_not_start_without_api_token(
    value: str | None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = _api_env()
    if value is None:
        del env["API_TOKEN"]
    else:
        env["API_TOKEN"] = value
    with pytest.raises(ConfigError, match="API_TOKEN"):
        load_settings(env, component="api")
    monkeypatch.setattr(os, "environ", env)
    assert api_main.main() == 2
    assert "API_TOKEN is not set" in capsys.readouterr().err


def test_api_settings_do_not_need_a_bot_token() -> None:
    settings = load_settings(_api_env(API_PORT="9000", API_DAILY_DOCS="7"), component="api")
    assert (settings.api_host, settings.api_port, settings.api_daily_docs) == ("127.0.0.1", 9000, 7)
    assert settings.bot_token == ""
    assert TOKEN not in repr(settings)
    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        load_settings(_api_env())  # the bot still needs its token


def test_build_app_refuses_an_empty_token(db_path: Path) -> None:
    async def main() -> None:
        async with open_storage(db_path) as storage:
            with pytest.raises(ValueError, match="API_TOKEN"):
                build_app(processor(storage), api_token="", max_file_bytes=MB)

    asyncio.run(main())


def test_server_serves_until_stopped(db_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings(
        bot_token="",
        gemini_api_key="",
        data_dir=db_path.parent,
        api_token=TOKEN,
        api_port=0,
        llm_fake=True,
    )
    caplog.set_level(logging.INFO)

    async def main() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(run(settings, stop))
        for _ in range(200):
            if "api starting" in caplog.text:
                break
            await asyncio.sleep(0.01)
        stop.set()
        await task

    asyncio.run(main())
    messages = [r.getMessage() for r in caplog.records]
    assert "LLM_FAKE is on: built-in synthetic answers, no model is called" in messages
    assert any(
        m.startswith("api starting: profile=compact model=fake-invoice-v1") for m in messages
    )
    assert messages[-1] == "api stopped"


# --- the fake model ------------------------------------------------------------------------


def test_fake_model_is_off_by_default() -> None:
    settings = load_settings({"BOT_TOKEN": "1:x", "GEMINI_API_KEY": "k"})
    assert settings.llm_fake is False
    for value in ("0", "false", "off", ""):
        assert (
            load_settings({"BOT_TOKEN": "1:x", "GEMINI_API_KEY": "k", "LLM_FAKE": value}).llm_fake
            is False
        )
    with pytest.raises(ConfigError, match="LLM_FAKE"):
        load_settings({"BOT_TOKEN": "1:x", "GEMINI_API_KEY": "k", "LLM_FAKE": "maybe"})


def test_fake_model_waives_the_api_key_and_logs_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    settings = load_settings({"BOT_TOKEN": "1:x", "LLM_FAKE": "1"})
    assert settings.llm_fake is True

    async def main() -> None:
        import httpx

        async with httpx.AsyncClient() as client:
            caplog.set_level(logging.INFO)
            fake, _ = build_provider(settings, client)
            real, _ = build_provider(
                load_settings({"BOT_TOKEN": "1:x", "GEMINI_API_KEY": "k"}), client
            )
            assert isinstance(fake, FakeProvider)
            assert isinstance(real, RateLimitedProvider) and real.model == "gemini-3.5-flash-lite"

    asyncio.run(main())
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert [r.getMessage() for r in warnings] == [
        "LLM_FAKE is on: built-in synthetic answers, no model is called"
    ]


# --- import isolation ----------------------------------------------------------------------


def _loaded(module: str, prefixes: tuple[str, ...]) -> str:
    code = (
        f"import sys, {module}; "
        f"print(','.join(sorted(m for m in sys.modules if m.split('.')[0] in {prefixes!r} "
        f"or m in {prefixes!r})))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=REPO
    )
    return result.stdout.strip()


def test_bot_does_not_load_the_api_and_the_api_does_not_load_aiogram() -> None:
    assert _loaded("invoice_pilot.bot.app", ("invoice_pilot.api",)) == ""
    assert (
        _loaded("invoice_pilot.api.server", ("aiogram", "alembic", "openpyxl", "PIL", "pypdf"))
        == ""
    )
