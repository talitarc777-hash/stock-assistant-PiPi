"""Regression tests for Discord command discovery and account linking."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot.command_catalog import DISCORD_BOT_BUILD_ID, SUPPORTED_PREFIX_COMMANDS
from bot.formatter import format_help_message
from bot.main import bot, is_allowed, validate_command_registry


class BotCommandRegistryTests(unittest.TestCase):
    def test_runtime_registry_contains_every_published_command(self) -> None:
        registered = validate_command_registry()

        self.assertEqual(set(registered), set(SUPPORTED_PREFIX_COMMANDS))
        self.assertIn("link", registered)

    def test_help_publishes_link_and_deployment_version_commands(self) -> None:
        message = format_help_message("!")

        self.assertIn("!link CODE", message)
        self.assertIn("!version", message)
        self.assertTrue(DISCORD_BOT_BUILD_ID)

    def test_private_link_messages_are_not_blocked_by_server_channel_allowlist(self) -> None:
        private_context = SimpleNamespace(
            guild=None,
            channel=SimpleNamespace(id=999),
        )
        server_context = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=999),
        )

        with patch("bot.main.ALLOWED_CHANNEL_IDS", [123]):
            self.assertTrue(is_allowed(private_context))
            self.assertFalse(is_allowed(server_context))


if __name__ == "__main__":
    unittest.main()
