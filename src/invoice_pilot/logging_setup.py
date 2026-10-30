"""Process logging: stdout only (journald on the host), UTC timestamps, quiet libraries.

One INFO line per processed document comes from ``services.processing``; the
per-call INFO line of ``services.extraction`` is muted to keep it at one. The host
drops more than 200 lines per 30 s, so chatty library loggers are raised.
"""

import logging
import sys
import time
from typing import Final

FORMAT: Final = "%(asctime)s %(levelname)s %(name)s %(message)s"
DATE_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"

#: Logger name -> minimum level.
QUIET_LOGGERS: Final[dict[str, int]] = {
    "aiogram.event": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "pypdf": logging.ERROR,
    "aiosqlite": logging.WARNING,
    "sqlalchemy": logging.WARNING,
    "invoice_pilot.services.extraction": logging.WARNING,
}


class UTCFormatter(logging.Formatter):
    converter = time.gmtime


def configure_logging(level: str = "INFO") -> None:
    """Replace the root handlers with one stdout handler and apply the quiet levels."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(UTCFormatter(FORMAT, DATE_FORMAT))
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    apply_quiet_levels()


def apply_quiet_levels() -> None:
    """Raise library loggers to their minimum level, whatever the root level is.

    Even with LOG_LEVEL=DEBUG this keeps out aiosqlite and SQLAlchemy debug lines,
    which would print SQL parameters (extracted fields).
    """
    for name, minimum in QUIET_LOGGERS.items():
        logging.getLogger(name).setLevel(minimum)
