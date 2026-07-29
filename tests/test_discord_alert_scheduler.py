"""Tests for the independent Discord alert scheduler."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.services.discord_alert_scheduler import DiscordAlertSchedulerService
from app.services.market_hours_service import MarketHoursState


class DiscordAlertSchedulerTests(unittest.TestCase):
    def _settings(self, webhook_url: str | None = "https://discord.invalid/webhook"):
        return SimpleNamespace(
            discord_alert_scheduler_enabled=True,
            discord_webhook_url=webhook_url,
        )

    @patch("app.services.discord_alert_scheduler.get_discord_alert_delivery_service")
    @patch("app.services.discord_alert_scheduler.get_market_hours_state")
    @patch("app.services.discord_alert_scheduler.get_settings")
    def test_start_and_stop_manage_independent_background_thread(
        self,
        mock_settings,
        mock_market_state,
        mock_delivery_service,
    ) -> None:
        mock_settings.return_value = self._settings()
        mock_market_state.return_value = MarketHoursState(
            now_utc=SimpleNamespace(),
            now_et=SimpleNamespace(),
            is_weekend=False,
            is_market_open=True,
            mode="market_open",
            interval_seconds=300,
        )
        mock_delivery_service.return_value.get_audit_health.return_value = {
            "delivery_counts": {"pending": 0, "sent": 0, "failed": 0},
            "last_delivery_status": None,
            "last_delivery_time_utc": None,
            "last_delivery_error": None,
            "last_delivery_attempt_count": 0,
            "last_delivery_http_status": None,
        }
        service = DiscordAlertSchedulerService()
        service._run_loop = MagicMock()  # pylint: disable=protected-access

        service.start()
        assert service._thread is not None  # pylint: disable=protected-access
        service._thread.join(timeout=1)  # pylint: disable=protected-access
        self.assertTrue(service.get_health()["scheduler_started"])

        service.stop()
        self.assertFalse(service.get_health()["scheduler_started"])

    @patch("app.services.discord_alert_scheduler.get_discord_alert_delivery_service")
    @patch("app.services.discord_alert_scheduler.collect_real_market_activity_alerts")
    @patch("app.services.discord_alert_scheduler.collect_overall_score_alerts")
    @patch("app.services.discord_alert_scheduler.get_user_profile_store")
    @patch("app.services.discord_alert_scheduler.get_settings")
    def test_cycle_collects_batches_and_never_runs_trader(
        self,
        mock_settings,
        mock_profile_store,
        mock_score_collect,
        mock_market_collect,
        mock_delivery_service,
    ) -> None:
        mock_settings.return_value = self._settings()
        mock_profile_store.return_value.list_alert_enabled_user_summaries.return_value = [
            SimpleNamespace(
                user_id="u1",
                preferred_delivery_source="discord",
                alert_watchlist=["AAPL", "NVDA"],
            )
        ]
        mock_score_collect.return_value = [
            SimpleNamespace(
                user_id="u1",
                ticker="AAPL",
                state_key="score-state",
                message="high score",
            )
        ]
        mock_market_collect.return_value = [
            SimpleNamespace(
                user_id="u1",
                ticker="NVDA",
                alert_type="sudden_price_move",
                pressure="buying_pressure",
                state_key="market-state",
                message="sudden rise",
            )
        ]
        delivery = MagicMock()
        delivery.deliver.return_value = SimpleNamespace(
            alerts_sent=2,
            batches_failed=0,
        )
        delivery.get_audit_health.return_value = {
            "delivery_counts": {"pending": 0, "sent": 1, "failed": 0},
            "last_delivery_status": "sent",
            "last_delivery_time_utc": "2026-07-28T00:00:00+00:00",
            "last_delivery_error": None,
            "last_delivery_attempt_count": 1,
            "last_delivery_http_status": 204,
        }
        mock_delivery_service.return_value = delivery

        result = DiscordAlertSchedulerService().run_cycle(
            source="test",
            user_ids=["u1"],
        )

        self.assertEqual(result["last_users_scanned"], 1)
        self.assertEqual(result["last_alerts_detected"], 2)
        self.assertEqual(result["last_alerts_sent"], 2)
        delivered_items = delivery.deliver.call_args.kwargs["items"]
        self.assertEqual({item.ticker for item in delivered_items}, {"AAPL", "NVDA"})
        self.assertEqual(
            {item.rule for item in delivered_items},
            {
                "score_above_threshold_discord",
                "real_market_sudden_price_move_buying_pressure",
            },
        )

    @patch("app.services.discord_alert_scheduler.get_discord_alert_delivery_service")
    @patch("app.services.discord_alert_scheduler.collect_overall_score_alerts")
    @patch("app.services.discord_alert_scheduler.get_user_profile_store")
    @patch("app.services.discord_alert_scheduler.get_settings")
    def test_missing_webhook_fails_closed_without_market_scan(
        self,
        mock_settings,
        mock_profile_store,
        mock_score_collect,
        mock_delivery_service,
    ) -> None:
        mock_settings.return_value = self._settings(webhook_url=None)
        mock_delivery_service.return_value.get_audit_health.return_value = {
            "delivery_counts": {"pending": 0, "sent": 0, "failed": 0},
            "last_delivery_status": None,
            "last_delivery_time_utc": None,
            "last_delivery_error": None,
            "last_delivery_attempt_count": 0,
            "last_delivery_http_status": None,
        }

        result = DiscordAlertSchedulerService().run_cycle(source="test")

        self.assertIn("DISCORD_WEBHOOK_URL", result["last_error"])
        self.assertEqual(result["last_users_scanned"], 0)
        mock_profile_store.assert_not_called()
        mock_score_collect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
