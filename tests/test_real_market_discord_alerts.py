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
            now_utc=datetime(2026, 6, 29, 14, 50, tzinfo=UTC),
        )

        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertEqual(alert.pressure, "buying_pressure")
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
            now_utc=datetime(2026, 6, 29, 14, 50, tzinfo=UTC),
        )

        self.assertIsNone(alert)


if __name__ == "__main__":
    unittest.main()
