"""API tests for secret-free Discord alert health reporting."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app


class DiscordAlertApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    @patch("app.api.discord_alerts.get_discord_alert_scheduler_service")
    def test_health_does_not_expose_webhook(self, mock_service) -> None:
        service = MagicMock()
        service.get_health.return_value = {
            "enabled": True,
            "webhook_configured": True,
            "healthy": True,
            "scheduler_started": True,
            "running": False,
            "mode": "market_open",
            "cadence_seconds": 300,
            "last_scan_time_utc": "2026-07-28T00:00:00+00:00",
            "next_scan_time_utc": "2026-07-28T00:05:00+00:00",
            "last_users_scanned": 1,
            "last_alerts_detected": 2,
            "last_alerts_sent": 2,
            "last_batches_failed": 0,
            "consecutive_failures": 0,
            "last_error": None,
            "delivery_counts": {"pending": 0, "sent": 2, "failed": 0},
            "last_delivery_status": "sent",
            "last_delivery_time_utc": "2026-07-28T00:00:00+00:00",
            "last_delivery_error": None,
            "last_delivery_attempt_count": 1,
            "last_delivery_http_status": 204,
        }
        mock_service.return_value = service

        response = self.client.get("/discord-alerts/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["healthy"])
        self.assertTrue(payload["webhook_configured"])
        self.assertNotIn("webhook_url", payload)


if __name__ == "__main__":
    unittest.main()
