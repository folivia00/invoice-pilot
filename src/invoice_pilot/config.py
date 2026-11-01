"""Settings of the bot and API processes, read once from environment variables.

The only settings module of the project. Secrets (``BOT_TOKEN``, ``GEMINI_API_KEY``,
``API_TOKEN``) never appear in ``repr``/``str`` or in error messages. The bot needs
``BOT_TOKEN``, the HTTP API needs ``API_TOKEN``; both need ``GEMINI_API_KEY`` unless
``LLM_FAKE`` is on. File limits and the number
of parallel extractions come from the profile (``PROFILE=compact|full``), not from
separate variables. ``ENV_VARS`` lists every variable; tests keep ``.env.example``
and the README in sync with it.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, Literal

from invoice_pilot.llm.pricing import PRICES

Profile = Literal["compact", "full"]

#: Every environment variable read by ``load_settings`` (order = .env.example order).
ENV_VARS: Final[tuple[str, ...]] = (
    "BOT_TOKEN",
    "GEMINI_API_KEY",
    "PROFILE",
    "DATA_DIR",
    "ADMIN_IDS",
    "LLM_MODEL",
    "LLM_RPM",
    "USER_DAILY_DOCS",
    "GLOBAL_DAILY_DOCS",
    "GLOBAL_DAILY_BUDGET_USD",
    "QUEUE_MAX",
    "RETENTION_DAYS",
    "LOG_LEVEL",
    "API_TOKEN",
    "API_HOST",
    "API_PORT",
    "API_DAILY_DOCS",
    "LLM_FAKE",
)
#: Variables the bot process cannot start without (``LLM_FAKE`` waives the API key).
REQUIRED_VARS: Final[tuple[str, ...]] = ("BOT_TOKEN", "GEMINI_API_KEY")
#: Variables the HTTP API process cannot start without.
API_REQUIRED_VARS: Final[tuple[str, ...]] = ("API_TOKEN", "GEMINI_API_KEY")

Component = Literal["bot", "api"]

DEFAULT_DATA_DIR: Final = "./data"
DEFAULT_LLM_MODEL: Final = "gemini-3.5-flash-lite"
#: Gemini free tier allows 15 requests per minute per project; one request of headroom.
DEFAULT_LLM_RPM: Final = 14
DEFAULT_USER_DAILY_DOCS: Final = 5
#: Gemini free tier allows 500 requests per day; 300 documents leave room for retries.
DEFAULT_GLOBAL_DAILY_DOCS: Final = 300
DEFAULT_GLOBAL_DAILY_BUDGET_USD: Final = Decimal("0.50")
DEFAULT_RETENTION_DAYS: Final = 7
#: Uploads allowed to wait for a processing slot (the one being processed not counted).
DEFAULT_QUEUE_MAX: Final = 10
LOG_LEVELS: Final[frozenset[str]] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
DEFAULT_API_HOST: Final = "127.0.0.1"
DEFAULT_API_PORT: Final = 8080
#: Documents the HTTP API accepts per UTC day (its own counter, see ``invoice_pilot.api``).
DEFAULT_API_DAILY_DOCS: Final = 100
_TRUE: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
_FALSE: Final[frozenset[str]] = frozenset({"0", "false", "no", "off"})

#: The Bot API refuses to download files above 20 MB (getFile limit).
TELEGRAM_DOWNLOAD_LIMIT_BYTES: Final = 20 * 1024 * 1024
_MB: Final = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ProfileLimits:
    """What a profile allows: file size, pages, parallel extractions."""

    max_file_bytes: int
    max_pages: int
    concurrency: int


PROFILES: Final[dict[str, ProfileLimits]] = {
    "compact": ProfileLimits(max_file_bytes=5 * _MB, max_pages=5, concurrency=1),
    "full": ProfileLimits(max_file_bytes=10 * _MB, max_pages=5, concurrency=4),
}


class ConfigError(ValueError):
    """Invalid or missing setting; the message names the variable, never its value."""


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str = field(repr=False)
    gemini_api_key: str = field(repr=False)
    profile: Profile = "compact"
    data_dir: Path = Path(DEFAULT_DATA_DIR)
    admin_ids: frozenset[int] = frozenset()
    llm_model: str = DEFAULT_LLM_MODEL
    llm_rpm: int = DEFAULT_LLM_RPM
    user_daily_docs: int = DEFAULT_USER_DAILY_DOCS
    global_daily_docs: int = DEFAULT_GLOBAL_DAILY_DOCS
    global_daily_budget_usd: Decimal = DEFAULT_GLOBAL_DAILY_BUDGET_USD
    queue_max: int = DEFAULT_QUEUE_MAX
    retention_days: int = DEFAULT_RETENTION_DAYS
    log_level: str = "INFO"
    api_token: str = field(default="", repr=False)
    api_host: str = DEFAULT_API_HOST
    api_port: int = DEFAULT_API_PORT
    api_daily_docs: int = DEFAULT_API_DAILY_DOCS
    #: Built-in synthetic answers instead of the model (checks without keys; off by default).
    llm_fake: bool = False

    @property
    def limits(self) -> ProfileLimits:
        return PROFILES[self.profile]

    @property
    def max_file_bytes(self) -> int:
        return self.limits.max_file_bytes

    @property
    def max_pages(self) -> int:
        return self.limits.max_pages

    @property
    def concurrency(self) -> int:
        return self.limits.concurrency

    @property
    def database_path(self) -> Path:
        return self.data_dir / "invoice_pilot.db"


def _get(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _required(env: Mapping[str, str], name: str) -> str:
    value = _get(env, name)
    if value is None:
        raise ConfigError(f"required environment variable {name} is not set")
    return value


def _int(env: Mapping[str, str], name: str, default: int, *, minimum: int) -> int:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    return value


def _decimal(env: Mapping[str, str], name: str, default: Decimal) -> Decimal:
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ConfigError(f"{name} must be a decimal number") from None
    if not value.is_finite() or value < 0:
        raise ConfigError(f"{name} must be a non-negative number")
    return value


def _bool(env: Mapping[str, str], name: str) -> bool:
    raw = _get(env, name)
    if raw is None or raw.lower() in _FALSE:
        return False
    if raw.lower() in _TRUE:
        return True
    raise ConfigError(f"{name} must be 1 or 0")


def _admin_ids(env: Mapping[str, str]) -> frozenset[int]:
    raw = _get(env, "ADMIN_IDS")
    if raw is None:
        return frozenset()
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            raise ConfigError("ADMIN_IDS must be comma-separated Telegram user IDs") from None
    return frozenset(ids)


def load_settings(env: Mapping[str, str], component: Component = "bot") -> Settings:
    """Build settings for ``component`` from ``env`` (normally ``os.environ``).

    Raise ``ConfigError`` naming the first missing or invalid variable.
    """
    llm_fake = _bool(env, "LLM_FAKE")
    bot_token = _required(env, "BOT_TOKEN") if component == "bot" else _get(env, "BOT_TOKEN")
    api_token = _required(env, "API_TOKEN") if component == "api" else _get(env, "API_TOKEN")
    gemini_api_key = _get(env, "GEMINI_API_KEY") if llm_fake else _required(env, "GEMINI_API_KEY")
    profile = (_get(env, "PROFILE") or "compact").lower()
    if profile not in PROFILES:
        raise ConfigError("PROFILE must be compact or full")
    model = _get(env, "LLM_MODEL") or DEFAULT_LLM_MODEL
    if model not in {price.model for price in PRICES}:
        raise ConfigError("LLM_MODEL has no configured price (invoice_pilot.llm.pricing)")
    log_level = (_get(env, "LOG_LEVEL") or "INFO").upper()
    if log_level not in LOG_LEVELS:
        raise ConfigError("LOG_LEVEL must be one of " + ", ".join(sorted(LOG_LEVELS)))
    return Settings(
        bot_token=bot_token or "",
        gemini_api_key=gemini_api_key or "",
        profile=profile,  # type: ignore[arg-type]
        data_dir=Path(_get(env, "DATA_DIR") or DEFAULT_DATA_DIR),
        admin_ids=_admin_ids(env),
        llm_model=model,
        llm_rpm=_int(env, "LLM_RPM", DEFAULT_LLM_RPM, minimum=1),
        user_daily_docs=_int(env, "USER_DAILY_DOCS", DEFAULT_USER_DAILY_DOCS, minimum=0),
        global_daily_docs=_int(env, "GLOBAL_DAILY_DOCS", DEFAULT_GLOBAL_DAILY_DOCS, minimum=0),
        global_daily_budget_usd=_decimal(
            env, "GLOBAL_DAILY_BUDGET_USD", DEFAULT_GLOBAL_DAILY_BUDGET_USD
        ),
        queue_max=_int(env, "QUEUE_MAX", DEFAULT_QUEUE_MAX, minimum=0),
        retention_days=_int(env, "RETENTION_DAYS", DEFAULT_RETENTION_DAYS, minimum=1),
        log_level=log_level,
        api_token=api_token or "",
        api_host=_get(env, "API_HOST") or DEFAULT_API_HOST,
        api_port=_port(env),
        api_daily_docs=_int(env, "API_DAILY_DOCS", DEFAULT_API_DAILY_DOCS, minimum=0),
        llm_fake=llm_fake,
    )


def _port(env: Mapping[str, str]) -> int:
    port = _int(env, "API_PORT", DEFAULT_API_PORT, minimum=1)
    if port > 65535:
        raise ConfigError("API_PORT must be at most 65535")
    return port
