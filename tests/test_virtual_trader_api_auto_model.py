"""Tests for automatic model selection in live virtual trader API routes."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.api.virtual_trader import run_virtual_trader_now
from app.models.live_virtual_trader import LiveTraderRunRequest
from app.services.live_virtual_trader import AUTO_TRADING_MODEL_NAME


class VirtualTraderApiAutoModelTests(unittest.TestCase):
    """Verify live trading ignores user-selected model names."""

    @patch("app.api.virtual_trader.clear_user_virtual_account_cache")
    @patch("app.api.virtual_trader.get_trader_scheduler_service")
    def test_run_now_forces_auto_best_model(
        self,
        mock_scheduler_service,
        _mock_clear_cache,
    ) -> None:
        mock_scheduler_service.return_value.run_user_now.return_value = SimpleNamespace(
            user_id="demo",
            model_name=AUTO_TRADING_MODEL_NAME,
            generated_at_utc="2026-06-01T00:00:00+00:00",
            account={
                "cash": 1000.0,
                "realized_pnl": 0.0,
                "total_contributions_applied": 1000.0,
                "holdings_value": 0.0,
                "total_equity": 1000.0,
            },
            holdings=[],
            latest_decisions=[],
            contribution_events=[],
            universe_size=0,
            tickers_evaluated=0,
            tickers_failed=0,
            fallback_used_count=0,
            equity_curve=[],
        )

        run_virtual_trader_now(
            LiveTraderRunRequest(
                user_id="demo",
                tickers=["VOO"],
                model_name="linear_regression",
            )
        )

        mock_scheduler_service.return_value.run_user_now.assert_called_once_with(
            user_id="demo",
            tickers=["VOO"],
            model_name=AUTO_TRADING_MODEL_NAME,
        )


if __name__ == "__main__":
    unittest.main()
