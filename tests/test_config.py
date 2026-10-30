"""Settings: required variables, secrets never shown, profile limits, docs in sync."""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from invoice_pilot.config import (
    ENV_VARS,
    PROFILES,
    REQUIRED_VARS,
    TELEGRAM_DOWNLOAD_LIMIT_BYTES,
    ConfigError,
    load_settings,
)

REPO = Path(__file__).resolve().parents[1]
TOKEN = "123456:FAKE-bot-token-SECRET"
KEY = "fake-gemini-key-SECRET"


def env(**extra: str) -> dict[str, str]:
    return {"BOT_TOKEN": TOKEN, "GEMINI_API_KEY": KEY, **extra}


@pytest.mark.parametrize("name", REQUIRED_VARS)
def test_missing_required_variable_names_it(name: str) -> None:
    values = env()
    del values[name]
    with pytest.raises(ConfigError, match=name):
        load_settings(values)


@pytest.mark.parametrize("name", REQUIRED_VARS)
def test_blank_required_variable_is_missing(name: str) -> None:
    with pytest.raises(ConfigError, match=name):
        load_settings(env(**{name: "   "}))


def test_defaults() -> None:
    settings = load_settings(env())
    assert settings.profile == "compact"
    assert settings.data_dir == Path("./data")
    assert settings.database_path == Path("./data") / "invoice_pilot.db"
    assert settings.admin_ids == frozenset()
    assert settings.llm_model == "gemini-3.5-flash-lite"
    assert settings.llm_rpm == 14
    assert settings.user_daily_docs == 5
    assert settings.global_daily_docs == 300
    assert settings.global_daily_budget_usd == Decimal("0.50")
    assert settings.retention_days == 7
    assert settings.log_level == "INFO"


def test_profiles() -> None:
    compact = load_settings(env())
    full = load_settings(env(PROFILE="full"))
    assert (compact.max_file_bytes, compact.max_pages, compact.concurrency) == (5 * 2**20, 5, 1)
    assert (full.max_file_bytes, full.max_pages, full.concurrency) == (10 * 2**20, 5, 4)
    assert all(p.max_file_bytes < TELEGRAM_DOWNLOAD_LIMIT_BYTES for p in PROFILES.values())


def test_values_are_parsed() -> None:
    settings = load_settings(
        env(
            ADMIN_IDS=" 1, 22 ,,333",
            LLM_RPM="3",
            GLOBAL_DAILY_BUDGET_USD="1.25",
            LOG_LEVEL="warning",
            DATA_DIR="/var/lib/invoicepilot/data",
        )
    )
    assert settings.admin_ids == frozenset({1, 22, 333})
    assert settings.llm_rpm == 3
    assert settings.global_daily_budget_usd == Decimal("1.25")
    assert settings.log_level == "WARNING"
    assert settings.data_dir == Path("/var/lib/invoicepilot/data")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PROFILE", "huge"),
        ("LLM_RPM", "0"),
        ("LLM_RPM", "many"),
        ("USER_DAILY_DOCS", "-1"),
        ("GLOBAL_DAILY_BUDGET_USD", "abc"),
        ("GLOBAL_DAILY_BUDGET_USD", "-1"),
        ("RETENTION_DAYS", "0"),
        ("ADMIN_IDS", "1,admin"),
        ("LOG_LEVEL", "LOUD"),
        ("LLM_MODEL", "unknown-model"),
    ],
)
def test_invalid_values_name_the_variable(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name) as info:
        load_settings(env(**{name: value}))
    assert TOKEN not in str(info.value) and KEY not in str(info.value)


def test_repr_and_str_hide_secrets() -> None:
    settings = load_settings(env())
    for text in (repr(settings), str(settings), f"{settings}"):
        assert TOKEN not in text
        assert KEY not in text
        assert "SECRET" not in text
    assert "profile='compact'" in repr(settings)


def _env_example_vars() -> list[str]:
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    return [line.split("=", 1)[0] for line in text.splitlines() if re.match(r"^[A-Z_]+=", line)]


def test_env_example_matches_config() -> None:
    names = _env_example_vars()
    assert len(names) == len(set(names))
    assert set(names) == set(ENV_VARS)


def test_env_example_has_no_secret_values() -> None:
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    for name in REQUIRED_VARS:
        assert re.search(rf"^{name}=$", text, re.MULTILINE)


def test_readme_configuration_table_matches_config() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Configuration", 1)[1].split("\n## ", 1)[0]
    names = re.findall(r"^\| `([A-Z_]+)` \|", section, re.MULTILINE)
    assert set(names) == set(ENV_VARS)
