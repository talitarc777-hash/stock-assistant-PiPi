"""Tests for market-hours helper logic used by the trader scheduler."""

from __future__ import annotations

from datetime import UTC, datetime
import unittest

from app.services.market_hours_service import (
    get_market_hours_state,
    get_scheduler_interval_seconds,
    is_market_open,
)


class MarketHoursServiceTests(unittest.TestCase):
    """Verify open/closed cadence logic using fixed UTC timestamps."""

    def test_is_market_open_true_during_us_session(self) -> None:
        # 2026-07-15 14:00 UTC -> 10:00 ET (weekday, open session)
        now_utc = datetime(2026, 7, 15, 14, 0, tzinfo=UTC)
        self.assertTrue(is_market_open(now_utc))
        self.assertEqual(get_scheduler_interval_seconds(now_utc), 300)

    def test_is_market_open_false_after_close(self) -> None:
        # 2026-07-15 21:00 UTC -> 17:00 ET (after close)
        now_utc = datetime(2026, 7, 15, 21, 0, tzinfo=UTC)
        self.assertFalse(is_market_open(now_utc))
        self.assertEqual(get_scheduler_interval_seconds(now_utc), 3600)

    def test_is_market_open_false_on_weekend(self) -> None:
        # 2026-07-18 is Saturday.
        now_utc = datetime(2026, 7, 18, 14, 0, tzinfo=UTC)
        state = get_market_hours_state(now_utc)
        self.assertTrue(state.is_weekend)
        self.assertFalse(state.is_market_open)
        self.assertEqual(state.interval_seconds, 3600)

    def test_hk_session_includes_lunch_break(self) -> None:
        morning_utc = datetime(2026, 8, 10, 2, 0, tzinfo=UTC)
        lunch_utc = datetime(2026, 8, 10, 4, 30, tzinfo=UTC)
        afternoon_utc = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)

        self.assertTrue(is_market_open(morning_utc, "HK"))
        self.assertFalse(is_market_open(lunch_utc, "HK"))
        self.assertTrue(is_market_open(afternoon_utc, "HK"))
        self.assertEqual(
            get_market_hours_state(morning_utc, "HK").timezone,
            "Asia/Hong_Kong",
        )


if __name__ == "__main__":
    unittest.main()
