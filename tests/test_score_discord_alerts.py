"""Tests for automatic high overall-score Discord alerts."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from app.services.score_discord_alerts import (
    build_overall_score_alert,
    scan_overall_score_discord_alerts,
)
from app.services.scoring import ScoreBreakdown


def _score(total: int = 80) -> ScoreBreakdown:
    momentum = 25 if total >= 80 else 20
    return ScoreBreakdown(
        trend_score=40,
        momentum_score=momentum,
        confirmation_score=15,
        risk_penalty=total - 40 - momentum - 15,
        total_score=total,
        label="strong watchlist candidate",
        action_summary="accumulate on pullbacks",
        explanations=[],
    )


class ScoreDiscordAlertsTests(unittest.TestCase):
    def test_builds_alert_at_threshold_and_explains_score_is_not_probability(self) -> None:
        alert = build_overall_score_alert(
            user_id="u1",
            ticker="aapl",
            score=_score(80),
            threshold=80,
            observed_date="2026-07-16",
            language="en",
        )

        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertEqual(alert.ticker, "AAPL")
        self.assertEqual(alert.state_key, "2026-07-16:80")
        self.assertIn("80/100", alert.message)
        self.assertIn("not an 80% profit probability", alert.message)

    def test_does_not_build_alert_below_threshold(self) -> None:
        alert = build_overall_score_alert(
            user_id="u1",
            ticker="AAPL",
            score=_score(75),
            threshold=80,
            observed_date="2026-07-16",
        )

        self.assertIsNone(alert)

    @patch("app.services.score_discord_alerts.send_discord_webhook_message")
    @patch("app.services.score_discord_alerts.score_from_indicators")
    @patch("app.services.score_discord_alerts.add_technical_indicators")
    @patch("app.services.score_discord_alerts.get_user_profile_store")
    @patch("app.services.score_discord_alerts.get_settings")
    def test_scan_sends_and_records_only_after_successful_delivery(
        self,
        mock_settings,
        mock_profile_store,
        mock_add_indicators,
        mock_score,
        mock_send,
    ) -> None:
        mock_settings.return_value = SimpleNamespace(
            discord_webhook_url="https://discord.invalid/webhook"
        )
        store = MagicMock()
        store.get_or_create_profile.return_value = SimpleNamespace(
            user_id="u1",
            alert_enabled=True,
            preferred_delivery_source="discord",
            preferred_language="bilingual",
            alert_threshold_high=80,
        )
        store.is_alert_state_new.return_value = True
        mock_profile_store.return_value = store
        indicator_frame = pd.DataFrame({"date": [pd.Timestamp("2026-07-16")]})
        mock_add_indicators.return_value = indicator_frame
        mock_score.return_value = _score(80)

        result = scan_overall_score_discord_alerts(
            user_id="u1",
            tickers=["aapl", "AAPL"],
            price_history_fn=lambda *_args: pd.DataFrame(),
        )

        self.assertEqual(len(result), 1)
        mock_send.assert_called_once_with(
            "https://discord.invalid/webhook",
            result[0].message,
        )
        store.record_alert_dispatched.assert_called_once_with(
            "u1",
            "AAPL",
            "score_above_threshold_discord",
            "2026-07-16:80",
        )

    @patch("app.services.score_discord_alerts.send_discord_webhook_message")
    @patch("app.services.score_discord_alerts.score_from_indicators")
    @patch("app.services.score_discord_alerts.add_technical_indicators")
    @patch("app.services.score_discord_alerts.get_user_profile_store")
    @patch("app.services.score_discord_alerts.get_settings")
    def test_failed_delivery_remains_retryable(
        self,
        mock_settings,
        mock_profile_store,
        mock_add_indicators,
        mock_score,
        mock_send,
    ) -> None:
        mock_settings.return_value = SimpleNamespace(
            discord_webhook_url="https://discord.invalid/webhook"
        )
        store = MagicMock()
        store.get_or_create_profile.return_value = SimpleNamespace(
            user_id="u1",
            alert_enabled=True,
            preferred_delivery_source="discord",
            preferred_language="en",
            alert_threshold_high=80,
        )
        store.is_alert_state_new.return_value = True
        mock_profile_store.return_value = store
        mock_add_indicators.return_value = pd.DataFrame(
            {"date": [pd.Timestamp("2026-07-16")]}
        )
        mock_score.return_value = _score(80)
        mock_send.side_effect = RuntimeError("temporary Discord failure")

        result = scan_overall_score_discord_alerts(
            user_id="u1",
            tickers=["AAPL"],
            price_history_fn=lambda *_args: pd.DataFrame(),
        )

        self.assertEqual(len(result), 1)
        store.record_alert_dispatched.assert_not_called()


if __name__ == "__main__":
    unittest.main()
