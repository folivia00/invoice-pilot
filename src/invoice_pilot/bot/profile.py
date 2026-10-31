"""The bot's public profile in Telegram, set at startup: command list and descriptions.

/stats is admin-only and stays out of the public command list. A failed call is a
warning: the bot works without its profile.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Final

from aiogram import Bot
from aiogram.types import BotCommand

from invoice_pilot.bot.messages import BOT_DESCRIPTION, BOT_SHORT_DESCRIPTION

logger = logging.getLogger(__name__)

PUBLIC_COMMANDS: Final[tuple[BotCommand, ...]] = (
    BotCommand(command="start", description="About the demo and today's limit"),
    BotCommand(command="export", description="Export your documents as CSV or XLSX"),
)


async def publish_profile(bot: Bot) -> int:
    """Set the commands and descriptions; return how many of the calls succeeded."""
    calls: tuple[tuple[str, Callable[[], Awaitable[bool]]], ...] = (
        ("set_my_commands", lambda: bot.set_my_commands(list(PUBLIC_COMMANDS))),
        (
            "set_my_short_description",
            lambda: bot.set_my_short_description(short_description=BOT_SHORT_DESCRIPTION),
        ),
        ("set_my_description", lambda: bot.set_my_description(description=BOT_DESCRIPTION)),
    )
    done = 0
    for name, call in calls:
        try:
            await call()
            done += 1
        except Exception as exc:  # the profile is cosmetic: never stop the start for it
            logger.warning("bot profile: %s failed: %s", name, type(exc).__name__)
    logger.info("bot profile: set %d of %d", done, len(calls))
    return done
