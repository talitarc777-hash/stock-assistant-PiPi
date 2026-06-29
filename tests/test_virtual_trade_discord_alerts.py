"""Tests for large virtual-trade Discord alert detection."""

from __future__ import annotations

import unittest

from app.services.virtual_trade_discord_alerts import build_virtual_trade_activity_alert


class VirtualTradeDiscordAlertTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
