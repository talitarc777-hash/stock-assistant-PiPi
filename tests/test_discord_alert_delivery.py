"""Tests for batched Discord alert delivery and persistent auditing."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.discord_alert_delivery import (
    DiscordAlertDeliveryService,
    DiscordAlertItem,
    build_discord_alert_batches,
)
from app.services.discord_webhook import (
    DiscordWebhookDeliveryError,
    DiscordWebhookDeliveryResult,
)


def _item(ticker: str, message: str, state: str) -> DiscordAlertItem:
    return DiscordAlertItem(
        user_id="u1",
        ticker=ticker,
        rule="score_above_threshold_discord",
        state_key=state,
        message=message,
    )


class DiscordAlertDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path("data") / f"test_discord_alert_delivery_{uuid4().hex}.db"
        self.profile_store = MagicMock()

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.db_path}{suffix}")
            if path.exists():
                path.unlink()

    def _settings(self, message_limit: int = 1900):
        return SimpleNamespace(
            discord_webhook_url="https://discord.invalid/webhook",
            discord_alert_message_limit=message_limit,
            discord_webhook_max_attempts=3,
            discord_webhook_retry_base_seconds=0.0,
        )

    def test_batch_builder_never_exceeds_limit(self) -> None:
        items = [_item("AAPL", "a" * 90, "s1"), _item("MSFT", "b" * 90, "s2")]
        batches = build_discord_alert_batches(items, message_limit=100)

        self.assertGreaterEqual(len(batches), 2)
        self.assertTrue(all(len(batch.content) <= 100 for batch in batches))

    @patch("app.services.discord_alert_delivery.get_settings")
    def test_success_records_audit_and_dedup_after_delivery(self, mock_settings) -> None:
        mock_settings.return_value = self._settings()
        sender = MagicMock(
            return_value=DiscordWebhookDeliveryResult(attempts=1, http_status=204)
        )
        service = DiscordAlertDeliveryService(
            str(self.db_path),
            sender=sender,
            profile_store=self.profile_store,
        )
        items = [_item("AAPL", "alert one", "s1"), _item("MSFT", "alert two", "s2")]

        result = service.deliver(user_id="u1", items=items, source="test")
        health = service.get_audit_health()

        self.assertEqual(result.alerts_sent, 2)
        self.assertEqual(result.batches_sent, 1)
        self.assertEqual(health["delivery_counts"]["sent"], 1)
        self.assertEqual(health["last_delivery_status"], "sent")
        self.assertEqual(self.profile_store.record_alert_dispatched.call_count, 2)

    @patch("app.services.discord_alert_delivery.get_settings")
    def test_partial_failure_only_acknowledges_fully_delivered_items(self, mock_settings) -> None:
        mock_settings.return_value = self._settings(message_limit=100)
        sender = MagicMock(
            side_effect=[
                DiscordWebhookDeliveryError("temporary", attempts=3, http_status=503),
                DiscordWebhookDeliveryResult(attempts=1, http_status=204),
            ]
        )
        service = DiscordAlertDeliveryService(
            str(self.db_path),
            sender=sender,
            profile_store=self.profile_store,
        )
        items = [_item("AAPL", "a" * 90, "s1"), _item("MSFT", "b" * 90, "s2")]

        result = service.deliver(user_id="u1", items=items, source="test")
        health = service.get_audit_health()

        self.assertEqual(result.alerts_sent, 1)
        self.assertEqual(result.batches_failed, 1)
        self.assertEqual(health["delivery_counts"], {"pending": 0, "sent": 1, "failed": 1})
        self.profile_store.record_alert_dispatched.assert_called_once_with(
            "u1",
            "MSFT",
            "score_above_threshold_discord",
            "s2",
        )


if __name__ == "__main__":
    unittest.main()
