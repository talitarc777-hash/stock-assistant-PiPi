"""Authoritative Discord command catalog and deploy-visible build identity."""

from __future__ import annotations


DISCORD_BOT_BUILD_ID = "2026.07.17-link-v1"

# Keep this catalog independent of discord.py so the backend readiness endpoint
# and deployment checks can report the command set without importing the bot.
SUPPORTED_PREFIX_COMMANDS: tuple[str, ...] = (
    "help",
    "version",
    "settings",
    "link",
    "syncstatus",
    "setlang",
    "setcompact",
    "setwatchlist",
    "addticker",
    "removeticker",
    "resetsettings",
    "analyze",
    "forecast",
    "watchlist",
    "alerts",
    "traderstatus",
    "lastrun",
    "nextrun",
    "modelstatus",
    "modelaccuracy",
    "shadowstatus",
    "virtualtrader",
    "runtrader",
    "lasttrades",
    "whytrade",
    "comparetrader",
    "account",
    "cashledger",
    "deposit",
    "withdraw",
    "setmonthly",
)


def prefixed_commands(prefix: str = "!") -> list[str]:
    """Return the deploy-visible command names with the configured prefix."""
    return [f"{prefix}{name}" for name in SUPPORTED_PREFIX_COMMANDS]
