"""Tests for one-time Discord/dashboard account linking."""

from pathlib import Path
import unittest
import uuid

from app.services.discord_link_service import (
    DiscordLinkService,
    DiscordLinkValidationError,
)


class DiscordLinkServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path("data") / f"test_discord_link_{uuid.uuid4().hex}.db"
        self.service = DiscordLinkService(db_path=str(self.db_path))

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()

    def test_code_is_one_time_and_resolves_profile(self) -> None:
        issued = self.service.create_link_code("web-profile")
        linked = self.service.consume_link_code(
            code=issued["code"],
            discord_user_id="12345",
            discord_display_name="Beginner",
        )

        self.assertTrue(linked["linked"])
        self.assertEqual(linked["profile_user_id"], "web-profile")
        self.assertEqual(
            self.service.resolve_profile_id("12345")["profile_user_id"],
            "web-profile",
        )
        with self.assertRaises(DiscordLinkValidationError):
            self.service.consume_link_code(
                code=issued["code"],
                discord_user_id="99999",
            )

    def test_unlinked_discord_user_falls_back_to_own_id(self) -> None:
        resolved = self.service.resolve_profile_id("67890")
        self.assertFalse(resolved["linked"])
        self.assertEqual(resolved["profile_user_id"], "67890")

    def test_new_link_replaces_old_profile_mapping(self) -> None:
        first = self.service.create_link_code("first-profile")
        self.service.consume_link_code(
            code=first["code"],
            discord_user_id="12345",
        )
        second = self.service.create_link_code("second-profile")
        self.service.consume_link_code(
            code=second["code"],
            discord_user_id="12345",
        )

        self.assertFalse(self.service.get_status_for_profile("first-profile")["linked"])
        self.assertEqual(
            self.service.resolve_profile_id("12345")["profile_user_id"],
            "second-profile",
        )


if __name__ == "__main__":
    unittest.main()
