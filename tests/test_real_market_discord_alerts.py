"""Tests for real-market Discord alert detection."""

from __future__ import annotations

from datetime import UTC, datetime
import unittest

import pandas as pd

from app.services.real_market_discord_alerts import build_real_market_activity_alert


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
