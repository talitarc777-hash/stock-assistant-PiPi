"""Tests for live market-data safeguards."""

from datetime import UTC, datetime
import unittest

import pandas as pd

from app.services.live_virtual_trader import _assess_market_data_quality


def _snapshot(date: str = "2026-07-17") -> dict:
    return {
        "price_timestamp": date,
        "open": 100.0,
        "high": 103.0,
        "low": 99.0,
        "close": 102.0,
        "volume": 1_000_000,
        "data_freshness_note": "Daily provider data.",
    }


class LiveVirtualTraderDataQualityTests(unittest.TestCase):
    def test_friday_data_is_accepted_on_monday(self) -> None:
        result = _assess_market_data_quality(
            snapshot=_snapshot("2026-07-17"),
            feature_row=pd.Series({"date": "2026-07-17", "close": 102.0}),
            now_utc=datetime(2026, 7, 20, 14, 0, tzinfo=UTC),
        )
        self.assertTrue(result["trade_safe"])
        self.assertEqual(result["business_day_age"], 1)

    def test_old_price_data_blocks_new_trade(self) -> None:
        result = _assess_market_data_quality(
            snapshot=_snapshot("2026-07-10"),
            feature_row=pd.Series({"date": "2026-07-10", "close": 102.0}),
            now_utc=datetime(2026, 7, 20, 14, 0, tzinfo=UTC),
        )
        self.assertFalse(result["trade_safe"])
        self.assertIn("price_older_than_two_business_days", result["reasons"])

    def test_invalid_ohlc_blocks_new_trade(self) -> None:
        snapshot = _snapshot()
        snapshot["high"] = 101.0
        result = _assess_market_data_quality(
            snapshot=snapshot,
            feature_row=pd.Series({"date": "2026-07-17", "close": 102.0}),
            now_utc=datetime(2026, 7, 17, 20, 0, tzinfo=UTC),
        )
        self.assertFalse(result["trade_safe"])
        self.assertIn("ohlc_above_reported_high", result["reasons"])

    def test_same_day_close_mismatch_blocks_new_trade(self) -> None:
        result = _assess_market_data_quality(
            snapshot=_snapshot(),
            feature_row=pd.Series({"date": "2026-07-17", "close": 95.0}),
            now_utc=datetime(2026, 7, 17, 20, 0, tzinfo=UTC),
        )
        self.assertFalse(result["trade_safe"])
        self.assertIn("same_day_close_mismatch", result["reasons"])


if __name__ == "__main__":
    unittest.main()
