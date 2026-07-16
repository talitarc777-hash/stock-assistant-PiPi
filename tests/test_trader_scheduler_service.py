"""Tests for the in-process trader scheduler service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.live_virtual_trader import LiveStatus
from app.services.market_hours_service import MarketHoursState
from app.services.trader_scheduler import TraderSchedulerBusyError, TraderSchedulerService


class TraderSchedulerServiceTests(unittest.TestCase):
    """Verify scheduler run accounting and overlap safety."""

    def _market_state_open(self) -> MarketHoursState:
        now = datetime(2026, 7, 15, 14, 0, tzinfo=UTC)
        return MarketHoursState(
            now_utc=now,
            now_et=now,
            is_weekend=False,
            is_market_open=True,
            mode="market_open",
            interval_seconds=300,
        )

    def _live_status(self, user_id: str) -> LiveStatus:
        return LiveStatus(
            user_id=user_id,
            model_name="logistic_regression",
            generated_at_utc="2026-07-15T14:00:00+00:00",
            account={"cash": 1000.0, "realized_pnl": 0.0, "total_contributions_applied": 1000.0},
            holdings=[],
            latest_decisions=[
                {"action": "buy", "ticker": "VOO"},
                {"action": "no_action", "ticker": "QQQ"},
            ],
            contribution_events=[],
            universe_size=120,
            tickers_evaluated=2,
            tickers_failed=0,
            fallback_used_count=0,
        )

    @patch("app.services.trader_scheduler.run_live_virtual_trader_now")
    @patch("app.services.trader_scheduler.get_user_profile_store")
    @patch("app.services.trader_scheduler.get_market_hours_state")
    def test_run_cycle_updates_status_counts(
        self,
        mock_market_state,
        mock_profile_store,
        mock_live_run,
    ) -> None:
        service = TraderSchedulerService()
        mock_market_state.return_value = self._market_state_open()
        mock_profile_store.return_value.list_alert_enabled_user_summaries.return_value = [
            SimpleNamespace(user_id="u1"),
            SimpleNamespace(user_id="u2"),
        ]
        mock_live_run.side_effect = [self._live_status("u1"), self._live_status("u2")]

        service.run_cycle(source="test", raise_if_busy=True)
        status = service.get_status(recent_hours=24)

        self.assertEqual(mock_live_run.call_args_list[0].kwargs["model_name"], None)
        self.assertEqual(mock_live_run.call_args_list[1].kwargs["model_name"], None)
        self.assertEqual(status["total_runs"], 1)
        self.assertEqual(status["last_users_processed"], 2)
        self.assertEqual(status["last_tickers_processed"], 4)
        self.assertEqual(status["last_tickers_failed"], 0)
        self.assertEqual(status["last_fallback_used"], 0)
        self.assertEqual(status["last_decisions_executed"], 2)
        self.assertTrue(status["recent_runs"])
        self.assertEqual(status["recent_runs"][0]["source"], "test")
        self.assertEqual(status["recent_runs"][0]["status"], "success")
        self.assertEqual(status["recent_runs"][0]["errors"], 0)

    @patch("app.services.trader_scheduler.scan_overall_score_discord_alerts")
    @patch("app.services.trader_scheduler.scan_real_market_activity_alerts")
    @patch("app.services.trader_scheduler.run_live_virtual_trader_now")
    @patch("app.services.trader_scheduler.get_user_profile_store")
    @patch("app.services.trader_scheduler.get_market_hours_state")
    def test_automatic_cycle_scans_users_market_alert_watchlist(
        self,
        mock_market_state,
        mock_profile_store,
        mock_live_run,
        mock_market_alert_scan,
        mock_score_alert_scan,
    ) -> None:
        service = TraderSchedulerService()
        mock_market_state.return_value = self._market_state_open()
        mock_profile_store.return_value.list_alert_enabled_user_summaries.return_value = [
            SimpleNamespace(user_id="u1", alert_watchlist=["TSLA", "NVDA"]),
        ]
        mock_live_run.return_value = self._live_status("u1")

        service.run_cycle(source="test", raise_if_busy=True)

        mock_market_alert_scan.assert_called_once_with(
            user_id="u1",
            tickers=["TSLA", "NVDA"],
        )
        mock_score_alert_scan.assert_called_once_with(
            user_id="u1",
            tickers=["TSLA", "NVDA"],
        )

    def test_run_user_now_raises_busy_when_locked(self) -> None:
        service = TraderSchedulerService()
        acquired = service._run_lock.acquire(blocking=False)  # pylint: disable=protected-access
        self.assertTrue(acquired)
        try:
            with self.assertRaises(TraderSchedulerBusyError):
                service.run_user_now(user_id="demo-user")
        finally:
            service._run_lock.release()  # pylint: disable=protected-access

    @patch("app.services.trader_scheduler.run_live_virtual_trader_now")
    def test_run_live_trader_with_retry_succeeds_on_second_attempt(self, mock_live_run) -> None:
        service = TraderSchedulerService()
        mock_live_run.side_effect = [RuntimeError("transient fetch error"), self._live_status("u1")]

        status = service._run_live_trader_with_retry(  # pylint: disable=protected-access
            user_id="u1",
            model_name="logistic_regression",
            max_attempts=2,
        )
        self.assertEqual(status.user_id, "u1")
        self.assertEqual(mock_live_run.call_count, 2)

    def test_health_snapshot_defaults(self) -> None:
        service = TraderSchedulerService()
        health = service.get_health()
        self.assertFalse(health["healthy"])
        self.assertFalse(health["scheduler_started"])

    def test_recent_runs_returns_only_last_24_hours(self) -> None:
        service = TraderSchedulerService()
        now = datetime.now(UTC).replace(microsecond=0)
        service._recent_runs.appendleft(  # pylint: disable=protected-access
            {
                "timestamp": now.isoformat(),
                "timestamp_utc": now.isoformat(),
                "source": "scheduler",
                "mode": "market_open",
                "users_processed": 1,
                "tickers_processed": 10,
                "tickers_failed": 0,
                "fallback_used": 0,
                "decisions_executed": 2,
                "status": "success",
                "errors": 0,
                "skipped": False,
                "message": "new-row",
                "note": "new-row",
                "error_count": 0,
                "error_messages": [],
            }
        )
        old_ts = (now - timedelta(hours=25)).isoformat()
        service._recent_runs.append(  # pylint: disable=protected-access
            {
                "timestamp": old_ts,
                "timestamp_utc": old_ts,
                "source": "scheduler",
                "mode": "market_closed",
                "users_processed": 1,
                "tickers_processed": 1,
                "tickers_failed": 0,
                "fallback_used": 0,
                "decisions_executed": 1,
                "status": "success",
                "errors": 0,
                "skipped": False,
                "message": "old-row",
                "note": "old-row",
                "error_count": 0,
                "error_messages": [],
            }
        )

        status = service.get_status(recent_hours=24)
        self.assertEqual(len(status["recent_runs"]), 1)
        self.assertEqual(status["recent_runs"][0]["message"], "new-row")


if __name__ == "__main__":
    unittest.main()
