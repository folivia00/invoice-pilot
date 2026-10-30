"""Entry point: ``uv run python -m invoice_pilot.bot``."""

import asyncio
import logging
import os
import sys

from invoice_pilot.bot.app import NotMigratedError, run
from invoice_pilot.config import ConfigError, load_settings
from invoice_pilot.logging_setup import configure_logging


def main() -> int:
    try:
        settings = load_settings(os.environ)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    try:
        asyncio.run(run(settings))
    except NotMigratedError as exc:
        logging.getLogger("invoice_pilot.bot").error("%s", exc)
        return 2
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
