"""Scheduled collection contracts for user-independent shadow evidence."""

from datetime import datetime
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.services.model_lifecycle_scheduler import ModelLifecycleSchedulerService


class _LifecycleStub:
    def __init__(self, rows):
        self.rows = rows
        self.state = {}

    def get_state(self, key):
        return self.state.get(key)

    def set_state(self, key, value):
        self.state[key] = value

    def list_registry(self, **kwargs):
        return self.rows


class ModelLifecycleShadowSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scheduler = ModelLifecycleSchedulerService()
        self.after_close = datetime(
            2026, 7, 14, 16, 45, tzinfo=ZoneInfo("America/New_York")
        )

    @patch("app.services.live_virtual_trader.collect_benchmark_shadow_observation")
    def test_collects_each_eligible_ticker_once_and_marks_day(self, mock_collect) -> None:
        mock_collect.return_value = {"recorded": True}
        lifecycle = _LifecycleStub([
            {"ticker": "SPY", "period": "10y", "model_name": "random_forest", "status": "candidate", "is_validated": True},
            {"ticker": "SPY", "period": "5y", "model_name": "random_forest", "status": "candidate", "is_validated": True},
            {"ticker": "GLOBAL", "period": "10y", "model_name": "random_forest", "status": "candidate", "is_validated": True},
            {"ticker": "QQQ", "period": "10y", "model_name": "random_forest", "status": "candidate", "is_validated": False},
        ])

        result = self.scheduler._collect_benchmark_shadows(  # pylint: disable=protected-access
            lifecycle=lifecycle,
            now_et=self.after_close,
        )

        self.assertEqual(result["attempted"], 1)
        self.assertEqual(result["recorded"], 1)
        mock_collect.assert_called_once_with(ticker="SPY", benchmark="VOO")
        self.assertEqual(lifecycle.state["shadow_collection_done_key"], "2026-07-14")

        second = self.scheduler._collect_benchmark_shadows(  # pylint: disable=protected-access
            lifecycle=lifecycle,
            now_et=self.after_close,
        )
        self.assertTrue(second["skipped"])

    @patch("app.services.live_virtual_trader.collect_benchmark_shadow_observation")
    def test_collection_waits_until_after_close(self, mock_collect) -> None:
        lifecycle = _LifecycleStub([
            {"ticker": "SPY", "period": "10y", "model_name": "random_forest", "status": "candidate", "is_validated": True},
        ])
        before_close = self.after_close.replace(hour=15)

        result = self.scheduler._collect_benchmark_shadows(  # pylint: disable=protected-access
            lifecycle=lifecycle,
            now_et=before_close,
        )

        self.assertTrue(result["skipped"])
        mock_collect.assert_not_called()

    @patch("app.services.live_virtual_trader.collect_benchmark_shadow_observation")
    def test_failed_provider_is_retried_in_later_cycle(self, mock_collect) -> None:
        mock_collect.side_effect = RuntimeError("market provider unavailable")
        lifecycle = _LifecycleStub([
            {"ticker": "SPY", "period": "10y", "model_name": "random_forest", "status": "candidate", "is_validated": True},
        ])

        result = self.scheduler._collect_benchmark_shadows(  # pylint: disable=protected-access
            lifecycle=lifecycle,
            now_et=self.after_close,
        )

        self.assertEqual(len(result["errors"]), 1)
        self.assertNotIn("shadow_collection_done_key", lifecycle.state)

    @patch("app.services.model_lifecycle_scheduler.get_model_lifecycle_service")
    def test_us_and_hk_workflow_completion_keys_are_independent(
        self,
        lifecycle_factory,
    ) -> None:
        lifecycle = _LifecycleStub([])
        lifecycle_factory.return_value = lifecycle
        us_close = datetime(
            2026, 7, 14, 16, 45, tzinfo=ZoneInfo("America/New_York")
        )
        hk_close = datetime(
            2026, 7, 14, 16, 45, tzinfo=ZoneInfo("Asia/Hong_Kong")
        )

        self.assertEqual(
            self.scheduler._due_workflow(us_close, "US")[0],
            "daily_incremental",
        )
        self.scheduler._mark_workflow_done("daily_incremental", us_close, "US")
        self.assertEqual(
            self.scheduler._due_workflow(hk_close, "HK")[0],
            "daily_incremental",
        )
        self.scheduler._mark_workflow_done("daily_incremental", hk_close, "HK")

        self.assertEqual(lifecycle.state["daily_done_key"], "2026-07-14")
        self.assertEqual(lifecycle.state["daily_done_key:HK"], "2026-07-14")


if __name__ == "__main__":
    unittest.main()
