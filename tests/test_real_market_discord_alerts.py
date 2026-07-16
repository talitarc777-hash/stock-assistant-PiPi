"""Tests for real-market Discord alert detection."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from app.services.real_market_discord_alerts import (
    RealMarketActivityAlert,
    build_real_market_activity_alert,
    scan_real_market_activity_alerts,
)


def _intraday_frame(
    *,
    prior_volume: int = 10_000,
    recent_volume: int = 80_000,
    recent_prices: list[float] | None = None,
) -> pd.DataFrame:
    prices = [100.0] * 12 + (recent_prices or [100.0, 101.0, 102.0, 103.0])
    volumes = [prior_volume] * 12 + [recent_volume] * 4
    timestamps = pd.date_range("2026-06-29 13:30:00+00:00", periods=len(prices), freq="5min")
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "close": prices,
            "volume": volumes,
        }
    )


class RealMarketDiscordAlertsTests(unittest.TestCase):
    @patch("app.services.real_market_discord_alerts.send_discord_webhook_message")
    @patch("app.services.real_market_discord_alerts.build_real_market_activity_alert")
    @patch("app.services.real_market_discord_alerts.get_user_profile_store")
    @patch("app.services.real_market_discord_alerts.get_settings")
    def test_scan_sends_real_market_alert_and_records_dedup_after_delivery(
        self,
        mock_settings,
        mock_profile_store,
        mock_build_alert,
        mock_send,
    ) -> None:
        mock_settings.return_value = SimpleNamespace(
            real_market_discord_alert_enabled=True,
            real_market_alert_ticker_limit=40,
            real_market_alert_window_minutes=15,
            real_market_large_value_threshold=10_000_000,
            real_market_volume_spike_multiplier=3.0,
            real_market_price_move_threshold_pct=1.5,
            real_market_min_window_volume=100_000,
            real_market_sudden_move_threshold_pct=10.0,
            discord_webhook_url="https://discord.invalid/webhook",
        )
        store = MagicMock()
        store.get_or_create_profile.return_value = SimpleNamespace(
            alert_enabled=True,
            preferred_delivery_source="discord",
            preferred_language="en",
        )
        store.is_alert_state_new.return_value = True
        mock_profile_store.return_value = store
        alert = RealMarketActivityAlert(
            user_id="u1",
            ticker="AAPL",
            alert_type="unusual_activity",
            pressure="buying_pressure",
            window_minutes=15,
            price_change_pct=2.0,
            latest_close=200.0,
            window_volume=100_000,
            average_window_volume=25_000,
            volume_spike_ratio=4.0,
            traded_value=20_000_000,
            threshold_value=10_000_000,
            price_threshold_pct=1.5,
            state_key="state-1",
            message="Unusual real-market buying pressure",
        )
        mock_build_alert.return_value = alert

        result = scan_real_market_activity_alerts(
            user_id="u1",
            tickers=["AAPL"],
            download_fn=lambda *_args: pd.DataFrame(),
        )

        self.assertEqual(result, [alert])
        mock_send.assert_called_once_with(
            "https://discord.invalid/webhook",
            alert.message,
        )
        store.record_alert_dispatched.assert_called_once_with(
            "u1",
            "AAPL",
            "real_market_unusual_activity_buying_pressure",
            "state-1",
        )

    def test_alerts_on_buying_pressure_volume_spike(self) -> None:
        alert = build_real_market_activity_alert(
            user_id="u1",
            ticker="AAPL",
            intraday_df=_intraday_frame(),
            window_minutes=15,
            large_value_threshold=1_000_000,
            volume_spike_multiplier=3.0,
            price_move_threshold_pct=1.5,
            min_window_volume=50_000,
            sudden_move_threshold_pct=4.0,
            now_utc=datetime(2026, 6, 29, 14, 50, tzinfo=UTC),
        )

        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertEqual(alert.pressure, "buying_pressure")
        self.assertEqual(alert.alert_type, "unusual_activity")
        self.assertGreater(alert.traded_value, 1_000_000)
        self.assertIn("unusual buying pressure", alert.message)

    def test_alerts_on_selling_pressure_volume_spike(self) -> None:
        alert = build_real_market_activity_alert(
            user_id="u1",
            ticker="TSLA",
            intraday_df=_intraday_frame(recent_prices=[103.0, 102.0, 101.0, 99.0]),
            window_minutes=15,
            large_value_threshold=1_000_000,
            volume_spike_multiplier=3.0,
            price_move_threshold_pct=1.5,
            min_window_volume=50_000,
            now_utc=datetime(2026, 6, 29, 14, 50, tzinfo=UTC),
        )

        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertEqual(alert.pressure, "selling_pressure")
        self.assertLess(alert.price_change_pct, 0)

    def test_no_alert_when_volume_is_normal(self) -> None:
        alert = build_real_market_activity_alert(
            user_id="u1",
            ticker="MSFT",
            intraday_df=_intraday_frame(prior_volume=40_000, recent_volume=45_000),
            window_minutes=15,
            large_value_threshold=1_000_000,
            volume_spike_multiplier=3.0,
            price_move_threshold_pct=1.5,
            min_window_volume=50_000,
            sudden_move_threshold_pct=4.0,
            now_utc=datetime(2026, 6, 29, 14, 50, tzinfo=UTC),
        )

        self.assertIsNone(alert)

    def test_sudden_price_move_alert_does_not_require_volume_spike(self) -> None:
        alert = build_real_market_activity_alert(
            user_id="u1",
            ticker="NVDA",
            intraday_df=_intraday_frame(
                prior_volume=40_000,
                recent_volume=45_000,
                recent_prices=[100.0, 101.0, 102.0, 104.0],
            ),
            window_minutes=15,
            large_value_threshold=100_000_000,
            volume_spike_multiplier=5.0,
            price_move_threshold_pct=1.5,
            min_window_volume=1_000_000,
            sudden_move_threshold_pct=3.0,
            now_utc=datetime(2026, 6, 29, 14, 50, tzinfo=UTC),
        )

        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertEqual(alert.alert_type, "sudden_price_move")
        self.assertIn("sharp rise", alert.message)

    def test_chinese_alert_is_traditional_chinese_only(self) -> None:
        alert = build_real_market_activity_alert(
            user_id="u1",
            ticker="TSLA",
            intraday_df=_intraday_frame(
                prior_volume=40_000,
                recent_volume=45_000,
                recent_prices=[104.0, 102.0, 101.0, 99.0],
            ),
            window_minutes=15,
            large_value_threshold=100_000_000,
            volume_spike_multiplier=5.0,
            price_move_threshold_pct=1.5,
            min_window_volume=1_000_000,
            sudden_move_threshold_pct=3.0,
            language="zh",
            now_utc=datetime(2026, 6, 29, 14, 50, tzinfo=UTC),
        )

        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertIn("市場價格急變警報", alert.message)
        self.assertIn("急跌", alert.message)
        self.assertIn("監察時段", alert.message)
        self.assertNotIn("Sudden market", alert.message)
        self.assertNotIn("Window", alert.message)


if __name__ == "__main__":
    unittest.main()
