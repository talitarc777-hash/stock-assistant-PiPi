"""End-to-end API contract tests for dashboard/Discord identity linking."""

from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.discord_link_service import DiscordLinkService


class DiscordLinkApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path("data") / f"test_discord_link_api_{uuid4().hex}.db"
        self.service = DiscordLinkService(db_path=str(self.db_path))
        self.service_patch = patch(
            "app.api.discord_link.get_discord_link_service",
            return_value=self.service,
        )
        self.service_patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.service_patch.stop()
        if self.db_path.exists():
            self.db_path.unlink()

    @patch.dict("os.environ", {}, clear=True)
    def test_readiness_reports_missing_configuration_without_secrets(self) -> None:
        response = self.client.get("/discord-link/readiness")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["fully_configured"])
        self.assertEqual(
            payload["missing_environment_variables"],
            ["DISCORD_BOT_TOKEN", "DISCORD_WEBHOOK_URL"],
        )
        self.assertTrue(payload["link_command_supported_by_build"])
        self.assertIn("!link", payload["supported_commands"])
        self.assertTrue(payload["bot_build_id"])
        self.assertNotIn("token", payload)
        self.assertNotIn("webhook_url", payload)

    @patch.dict(
        "os.environ",
        {"DISCORD_BOT_TOKEN": "secret-token", "DISCORD_WEBHOOK_URL": "secret-webhook"},
        clear=True,
    )
    def test_readiness_reports_configured_without_returning_secret_values(self) -> None:
        response = self.client.get("/discord-link/readiness")
        payload = response.json()
        self.assertTrue(payload["fully_configured"])
        self.assertEqual(payload["missing_environment_variables"], [])
        self.assertNotIn("secret-token", response.text)
        self.assertNotIn("secret-webhook", response.text)

    def test_link_resolve_status_and_unlink_share_one_profile(self) -> None:
        issued = self.client.post(
            "/discord-link/code",
            json={"profile_user_id": "web-beginner"},
        )
        self.assertEqual(issued.status_code, 200)
        code = issued.json()["code"]

        consumed = self.client.post(
            "/discord-link/consume",
            json={
                "code": code,
                "discord_user_id": "discord-123",
                "discord_display_name": "Beginner",
            },
        )
        self.assertEqual(consumed.status_code, 200)
        self.assertEqual(consumed.json()["profile_user_id"], "web-beginner")

        resolved = self.client.get(
            "/discord-link/resolve",
            params={"discord_user_id": "discord-123"},
        )
        status = self.client.get(
            "/discord-link/status",
            params={"profile_user_id": "web-beginner"},
        )
        self.assertTrue(resolved.json()["linked"])
        self.assertEqual(resolved.json()["profile_user_id"], "web-beginner")
        self.assertEqual(status.json()["discord_user_id"], "discord-123")

        unlinked = self.client.post(
            "/discord-link/unlink",
            json={"profile_user_id": "web-beginner"},
        )
        self.assertEqual(unlinked.status_code, 200)
        self.assertFalse(unlinked.json()["linked"])
        self.assertEqual(
            self.client.get(
                "/discord-link/resolve",
                params={"discord_user_id": "discord-123"},
            ).json()["profile_user_id"],
            "discord-123",
        )

    def test_invalid_or_reused_code_returns_clear_client_error(self) -> None:
        invalid = self.client.post(
            "/discord-link/consume",
            json={"code": "BADCODE1", "discord_user_id": "discord-123"},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("invalid", invalid.json()["detail"].lower())

        issued = self.service.create_link_code("web-beginner")
        payload = {"code": issued["code"], "discord_user_id": "discord-123"}
        self.assertEqual(self.client.post("/discord-link/consume", json=payload).status_code, 200)
        reused = self.client.post("/discord-link/consume", json=payload)
        self.assertEqual(reused.status_code, 400)
        self.assertIn("already been used", reused.json()["detail"].lower())

    def test_routes_are_registered_in_openapi(self) -> None:
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertIn("/discord-link/code", paths)
        self.assertIn("/discord-link/consume", paths)
        self.assertIn("/discord-link/resolve", paths)
        self.assertIn("/discord-link/status", paths)
        self.assertIn("/discord-link/unlink", paths)


if __name__ == "__main__":
    unittest.main()
