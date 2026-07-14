"""Tests for large virtual-trade Discord alert detection."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.virtual_trade_discord_alerts import (
    build_virtual_trade_activity_alert,
    maybe_send_virtual_trade_discord_alert,
)


class VirtualTradeDiscordAlertTests(unittest.TestCase):
    @staticmethod
    def _large_trade() -> dict:
        return {
            "timestamp": "2026-06-29T08:00:00+00:00",
            "user_id": "u1",
            "ticker": "AAPL",
            "action": "buy",
            "quantity": 100,
            "price": 600,
        }

    def test_alerts_on_large_single_buy(self) -> None:
        trade = {
            "timestamp": "2026-06-29T08:00:00+00:00",
            "user_id": "u1",
            "ticker": "AAPL",
            "action": "buy",
            "quantity": 100,
            "price": 600,
        }

        alert = build_virtual_trade_activity_alert(
            current_trade=trade,
            recent_trades=[trade],
            window_minutes=30,
            large_value_threshold_hkd=50_000,
            min_trade_count=2,
        )

        self.assertIsNotNone(alert)
        self.assertEqual(alert.ticker, "AAPL")
        self.assertEqual(alert.action, "buy")
        self.assertGreaterEqual(alert.total_value_hkd, 50_000)

    def test_alerts_on_short_window_sell_burst(self) -> None:
        current = {
            "timestamp": "2026-06-29T08:10:00+00:00",
            "user_id": "u1",
            "ticker": "MSFT",
            "action": "sell",
            "quantity": 25,
            "price": 1_000,
        }
        previous = {
            "timestamp": "2026-06-29T08:00:00+00:00",
            "user_id": "u1",
            "ticker": "MSFT",
            "action": "sell",
            "quantity": 30,
            "price": 1_000,
        }

        alert = build_virtual_trade_activity_alert(
            current_trade=current,
            recent_trades=[current, previous],
            window_minutes=30,
            large_value_threshold_hkd=50_000,
            min_trade_count=2,
        )

        self.assertIsNotNone(alert)
        self.assertEqual(alert.trade_count, 2)
        self.assertEqual(alert.total_value_hkd, 55_000)

    def test_ignores_no_action_and_small_trade(self) -> None:
        no_action = {
            "timestamp": "2026-06-29T08:00:00+00:00",
            "user_id": "u1",
            "ticker": "AAPL",
            "action": "no_action",
            "quantity": 0,
            "price": 600,
        }
        small_buy = {
            "timestamp": "2026-06-29T08:05:00+00:00",
            "user_id": "u1",
            "ticker": "AAPL",
            "action": "buy",
            "quantity": 5,
            "price": 600,
        }

        self.assertIsNone(
            build_virtual_trade_activity_alert(
                current_trade=no_action,
                recent_trades=[no_action],
                window_minutes=30,
                large_value_threshold_hkd=50_000,
                min_trade_count=2,
            )
        )
        self.assertIsNone(
            build_virtual_trade_activity_alert(
                current_trade=small_buy,
                recent_trades=[small_buy],
                window_minutes=30,
                large_value_threshold_hkd=50_000,
                min_trade_count=2,
            )
        )

    @patch("app.services.virtual_trade_discord_alerts.send_discord_webhook_message")
    @patch("app.services.virtual_trade_discord_alerts.get_user_profile_store")
    @patch("app.services.virtual_trade_discord_alerts.get_settings")
    def test_records_dedup_only_after_confirmed_webhook_delivery(
        self,
        mock_settings,
        mock_store_factory,
        mock_send,
    ) -> None:
        mock_settings.return_value = SimpleNamespace(
            virtual_trade_discord_alert_enabled=True,
            virtual_trade_alert_window_minutes=30,
            virtual_trade_large_value_hkd_threshold=50_000,
            virtual_trade_alert_min_trade_count=2,
            discord_webhook_url="https://discord.invalid/webhook",
        )
        store = Mock()
        store.get_or_create_profile.return_value = SimpleNamespace(
            alert_enabled=True,
            preferred_delivery_source="discord",
        )
        store.is_alert_state_new.return_value = True
        mock_store_factory.return_value = store
        trade = self._large_trade()

        maybe_send_virtual_trade_discord_alert(
            current_trade=trade,
            recent_trades=[trade],
        )

        mock_send.assert_called_once()
        store.record_alert_dispatched.assert_called_once()

    @patch("app.services.virtual_trade_discord_alerts.send_discord_webhook_message")
    @patch("app.services.virtual_trade_discord_alerts.get_user_profile_store")
    @patch("app.services.virtual_trade_discord_alerts.get_settings")
    def test_failed_webhook_remains_retryable(
        self,
        mock_settings,
        mock_store_factory,
        mock_send,
    ) -> None:
        mock_settings.return_value = SimpleNamespace(
            virtual_trade_discord_alert_enabled=True,
            virtual_trade_alert_window_minutes=30,
            virtual_trade_large_value_hkd_threshold=50_000,
            virtual_trade_alert_min_trade_count=2,
            discord_webhook_url="https://discord.invalid/webhook",
        )
        store = Mock()
        store.get_or_create_profile.return_value = SimpleNamespace(
            alert_enabled=True,
            preferred_delivery_source="discord",
        )
        store.is_alert_state_new.return_value = True
        mock_store_factory.return_value = store
        mock_send.side_effect = RuntimeError("temporary Discord failure")
        trade = self._large_trade()

        maybe_send_virtual_trade_discord_alert(
            current_trade=trade,
            recent_trades=[trade],
        )

        store.record_alert_dispatched.assert_not_called()

    @patch("app.services.virtual_trade_discord_alerts.send_discord_webhook_message")
    @patch("app.services.virtual_trade_discord_alerts.get_user_profile_store")
    @patch("app.services.virtual_trade_discord_alerts.get_settings")
    def test_shared_web_preference_can_disable_proactive_discord_delivery(
        self,
        mock_settings,
        mock_store_factory,
        mock_send,
    ) -> None:
        mock_settings.return_value = SimpleNamespace(
            virtual_trade_discord_alert_enabled=True,
            virtual_trade_alert_window_minutes=30,
            virtual_trade_large_value_hkd_threshold=50_000,
            virtual_trade_alert_min_trade_count=2,
            discord_webhook_url="https://discord.invalid/webhook",
        )
        store = Mock()
        store.get_or_create_profile.return_value = SimpleNamespace(
            alert_enabled=False,
            preferred_delivery_source="discord",
        )
        mock_store_factory.return_value = store
        trade = self._large_trade()

        result = maybe_send_virtual_trade_discord_alert(
            current_trade=trade,
            recent_trades=[trade],
        )

        self.assertIsNone(result)
        mock_send.assert_not_called()
        store.is_alert_state_new.assert_not_called()


if __name__ == "__main__":
    unittest.main()
