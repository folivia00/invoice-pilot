"""Minimal KEY=VALUE file reader for API keys (no dependency, values never logged)."""

from pathlib import Path


class EnvFileError(ValueError):
    """Problem with the env file; the message names variables, never values."""


def parse_env_file(path: Path) -> dict[str, str]:
    """Read ``KEY=VALUE`` lines; blank lines and ``#`` comments are ignored.

    An optional ``export `` prefix and matching single or double quotes around
    the value are stripped. Values are returned as-is otherwise.
    """
    if not path.is_file():
        raise EnvFileError(f"env file not found: {path}")
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or not key.replace("_", "").isalnum():
            raise EnvFileError(f"{path}:{number}: expected KEY=VALUE")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def require_key(values: dict[str, str], name: str, path: Path) -> str:
    """Non-empty value of ``name`` or an error that names the variable only."""
    value = values.get(name, "")
    if not value:
        raise EnvFileError(f"{name} is missing or empty in {path}")
    return value
