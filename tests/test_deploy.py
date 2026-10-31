"""Server deployment files: hashed requirements in sync with uv.lock, run.sh checks."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
REQUIREMENTS = REPO / "deploy" / "requirements.txt"
RUN_SH = REPO / "deploy" / "run.sh"
EXPORT = [
    "uv",
    "export",
    "--frozen",
    "--no-dev",
    "--no-emit-project",
    "--no-header",
    "--format",
    "requirements.txt",
]


def _pins(text: str) -> dict[str, str]:
    return dict(re.findall(r"^([a-z0-9][a-z0-9._-]*)==(\S+) \\$", text, re.MULTILINE))


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_requirements_match_uv_lock(tmp_path: Path) -> None:
    target = tmp_path / "requirements.txt"
    subprocess.run(
        [*EXPORT, "--output-file", str(target)], cwd=REPO, check=True, capture_output=True
    )
    assert target.read_text(encoding="utf-8") == REQUIREMENTS.read_text(encoding="utf-8"), (
        "deploy/requirements.txt is out of date: run `make deploy-requirements`"
    )


def test_requirements_are_runtime_only_pinned_and_hashed() -> None:
    text = REQUIREMENTS.read_text(encoding="utf-8")
    pins = _pins(text)
    assert {"aiogram", "aiohttp", "httpx", "openpyxl", "pypdf", "sqlalchemy", "alembic"} <= set(
        pins
    )
    assert pins["aiogram"] == "3.28.2"
    for name in ("pillow", "reportlab", "pytest", "hypothesis", "ruff", "invoice-pilot"):
        assert name not in pins
        assert not re.search(rf"^{name}\b", text, re.MULTILINE | re.IGNORECASE)
    requirements = re.split(r"\n(?=[a-z0-9])", text.strip())
    assert len(requirements) == len(pins)
    for block in requirements:
        assert "--hash=sha256:" in block, block.splitlines()[0]
    assert "-e " not in text and "file:" not in text


def test_run_sh_is_posix_and_sets_the_server_environment() -> None:
    text = RUN_SH.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert "\r" not in text
    assert "set -eu" in text
    for default in (
        'APP_DIR="${APP_DIR:-/var/lib/invoicepilot/app}"',
        'VENV="${VENV:-/var/lib/invoicepilot/.venv}"',
        'DATA_DIR="${DATA_DIR:-/var/lib/invoicepilot/data}"',
    ):
        assert default in text
    assert "MALLOC_MMAP_THRESHOLD_=131072" in text
    assert not re.search(r"^\s*(\.|source)\s", text, re.MULTILINE)  # never sources .env
    assert "/.env" not in text
    migrate = text.index('-m alembic -c "$APP_DIR/alembic.ini" upgrade head')
    assert migrate < text.index('exec "$VENV/bin/python" -m invoice_pilot.bot')
    assert text.rstrip().endswith('exec "$VENV/bin/python" -m invoice_pilot.bot')


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell")
@pytest.mark.parametrize(
    ("present", "missing"),
    [({}, "BOT_TOKEN GEMINI_API_KEY"), ({"BOT_TOKEN": "123:SECRET-token"}, "GEMINI_API_KEY")],
)
def test_run_sh_refuses_to_start_without_secrets(
    tmp_path: Path, present: dict[str, str], missing: str
) -> None:
    env = {
        k: v for k, v in os.environ.items() if k not in ("BOT_TOKEN", "GEMINI_API_KEY")
    } | present
    env |= {"APP_DIR": str(tmp_path), "VENV": str(tmp_path / "venv"), "DATA_DIR": str(tmp_path)}
    result = subprocess.run(
        ["sh", str(RUN_SH)], env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 2
    assert f"missing required variables: {missing}" in result.stderr
    assert "SECRET" not in result.stdout + result.stderr
    assert not (tmp_path / "invoice_pilot.db").exists()  # stopped before the migrations
