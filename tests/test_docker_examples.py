"""Docker files (Full profile) and the examples: hashed install, no secrets, one DB per process."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
N8N = REPO / "examples" / "n8n"


def _text(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def test_dockerfile_installs_hashed_runtime_wheels_and_runs_unprivileged() -> None:
    text = _text("Dockerfile")
    assert text.startswith("# ") and "\nFROM python:3.12-slim\n" in text
    install = re.search(r"pip install(.*?)-r /tmp/requirements.txt", text, re.DOTALL)
    assert install is not None
    for flag in ("--require-hashes", "--only-binary=:all:", "--no-cache-dir"):
        assert flag in install.group(1)
    assert "COPY deploy/requirements.txt /tmp/requirements.txt" in text
    assert "PYTHONPATH=/app/src" in text and "MALLOC_MMAP_THRESHOLD_=131072" in text
    assert re.search(r"^USER app$", text, re.MULTILINE)
    copied = re.findall(r"^COPY (\S+)", text, re.MULTILINE)
    assert copied == [
        "deploy/requirements.txt",
        "src",
        "migrations",
        "alembic.ini",
        "docker/entrypoint.sh",
    ]


def test_dockerignore_is_an_allow_list() -> None:
    lines = [line for line in _text(".dockerignore").splitlines() if line and line[0] != "#"]
    assert lines[0] == "*"
    allowed = {line[1:] for line in lines if line.startswith("!")}
    assert allowed == {
        "src/",
        "migrations/",
        "alembic.ini",
        "deploy/requirements.txt",
        "docker/entrypoint.sh",
    }


def test_entrypoint_migrates_then_execs_one_component() -> None:
    text = _text("docker/entrypoint.sh")
    assert text.startswith("#!/bin/sh\n") and "\r" not in text
    assert "set -eu" in text
    assert text.index("alembic -c /app/alembic.ini upgrade head") < text.index(
        'exec python -m "invoice_pilot.$component"'
    )
    stage = subprocess.run(
        ["git", "ls-files", "--stage", "docker/entrypoint.sh"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if stage:  # tracked: must be executable in the image
        assert stage.startswith("100755 ")


def test_compose_gives_bot_and_api_separate_databases() -> None:
    text = _text("docker-compose.yml")
    services = text.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    bot = services.split("\n  api:\n", 1)[0]
    api = services.split("\n  api:\n", 1)[1]
    assert "- bot-data:/data" in bot and "api-data" not in bot
    assert "- api-data:/data" in api and "bot-data" not in api
    assert 'command: ["bot"]' in bot and 'command: ["api"]' in api
    assert '"127.0.0.1:${API_PUBLISH_PORT:-8080}:8080"' in api  # not published on all interfaces
    assert "path: ${ENV_FILE:-.env}" in text
    for line in text.splitlines():  # no secrets written into the compose file
        assert not re.match(r"\s*(BOT_TOKEN|GEMINI_API_KEY|API_TOKEN)\s*:", line)


def _workflow(name: str) -> dict[str, object]:
    return json.loads((N8N / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["invoice-pilot-local.json", "invoice-pilot-gmail-sheets.json"])
def test_n8n_workflows_are_valid_and_hold_no_secrets(name: str) -> None:
    workflow = _workflow(name)
    nodes = workflow["nodes"]
    assert isinstance(nodes, list)
    names = {node["name"] for node in nodes}
    connections = workflow["connections"]
    assert isinstance(connections, dict)
    for source, outputs in connections.items():
        assert source in names
        for branch in outputs["main"]:
            assert all(link["node"] in names for link in branch)
    http = next(node for node in nodes if node["type"] == "n8n-nodes-base.httpRequest")
    body = http["parameters"]["bodyParameters"]["parameters"]
    assert body[0]["parameterType"] == "formBinaryData" and body[0]["name"] == "file"
    for node in nodes:
        for credential in node.get("credentials", {}).values():
            assert credential["id"].startswith("REPLACE_WITH_")
    text = (N8N / name).read_text(encoding="utf-8")
    assert re.findall(r"Bearer [^\s\"{]", text) == []  # a token only through $env or credentials


def test_local_workflow_needs_no_credentials() -> None:
    workflow = _workflow("invoice-pilot-local.json")
    nodes = workflow["nodes"]
    assert isinstance(nodes, list)
    assert all("credentials" not in node for node in nodes)
    assert nodes[0]["type"] == "n8n-nodes-base.manualTrigger"
    http = next(node for node in nodes if node["type"] == "n8n-nodes-base.httpRequest")
    header = http["parameters"]["headerParameters"]["parameters"][0]
    assert header == {
        "name": "Authorization",
        "value": "=Bearer {{ $env.INVOICE_PILOT_API_TOKEN }}",
    }


@pytest.mark.parametrize("name", ["inv-001.pdf", "inv-005.jpg", "inv-007.pdf"])
def test_example_invoices_are_unchanged_copies_of_the_dataset(name: str) -> None:
    example = (REPO / "examples" / "invoices" / name).read_bytes()
    original = (REPO / "eval" / "dataset" / "docs" / name).read_bytes()
    assert hashlib.sha256(example).digest() == hashlib.sha256(original).digest()


def test_server_install_skips_the_pip_cache() -> None:
    text = _text("deploy/README.md")
    assert "--no-cache-dir" in text and "rm -rf .cache/pip" in text
