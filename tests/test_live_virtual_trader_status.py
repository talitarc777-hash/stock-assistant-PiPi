"""Tests for lightweight live virtual trader status reads."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from app.api.virtual_trader import get_virtual_trader_live_sync
from app.services.live_virtual_trader import LiveStatus
from app.services.live_virtual_trader import get_live_virtual_trader_status
from app.services.live_virtual_trader import _build_model_inference_frame


class LiveVirtualTraderStatusTests(unittest.TestCase):
    """Verify status refreshes do not perform full-universe market scans."""

    def test_global_model_uses_pooled_training_feature_representation(self) -> None:
        regular = pd.Series({"close": 100.0, "close_vs_sma_20_pct": None})
        pooled = pd.Series({"close_vs_sma_20_pct": 3.5})

        global_frame = _build_model_inference_frame(
            latest_row=regular,
            stationary_latest_row=pooled,
            candidate_ticker="GLOBAL",
            feature_names=["close_vs_sma_20_pct"],
            stationary_features=True,
        )
        ticker_frame = _build_model_inference_frame(
            latest_row=regular,
            stationary_latest_row=pooled,
            candidate_ticker="AAPL",
            feature_names=["close"],
        )

        self.assertEqual(global_frame.iloc[0]["close_vs_sma_20_pct"], 3.5)
        self.assertEqual(ticker_frame.iloc[0]["close"], 100.0)

        legacy_global_frame = _build_model_inference_frame(
            latest_row=regular,
            stationary_latest_row=pooled,
            candidate_ticker="GLOBAL",
            feature_names=["close"],
            stationary_features=False,
        )
        self.assertEqual(legacy_global_frame.iloc[0]["close"], 100.0)

        ticker_stationary_frame = _build_model_inference_frame(
            latest_row=regular,
            stationary_latest_row=pooled,
            candidate_ticker="AAPL",
            feature_names=["close_vs_sma_20_pct"],
            stationary_features=True,
        )
        self.assertEqual(ticker_stationary_frame.iloc[0]["close_vs_sma_20_pct"], 3.5)

    @patch("app.services.live_virtual_trader.build_live_equity_curve")
    @patch("app.services.live_virtual_trader.get_live_virtual_trader_store")
    @patch("app.services.live_virtual_trader.get_account_ledger_service")
    @patch("app.services.live_virtual_trader._latest_prices_for_symbols")
    @patch("app.services.live_virtual_trader._resolve_user_tickers")
    def test_status_read_uses_account_prices_without_full_universe_fetch(
        self,
        mock_resolve_tickers,
        mock_latest_prices,
        mock_ledger_service,
        mock_store,
        mock_equity_curve,
    ) -> None:
        mock_resolve_tickers.return_value = ["VOO", "QQQ", "AAPL"]
        mock_ledger_service.return_value.build_account_summary.return_value = {
            "as_of": "2026-05-29T00:00:00+00:00",
            "cash": 1000.0,
            "realized_pnl": 0.0,
            "net_deposits": 1000.0,
            "holdings_value": 0.0,
            "total_account_value": 1000.0,
            "unrealized_pnl": 0.0,
            "holdings": [],
            "latest_prices": {},
        }
        mock_equity_curve.return_value = {
            "curve_last_point_timestamp": "2026-05-29T00:00:00+00:00",
            "latest_total_equity": 1000.0,
            "points": [],
        }
        mock_store.return_value.list_trades.return_value = []
        mock_ledger_service.return_value.list_events.return_value = []

        status = get_live_virtual_trader_status(
            user_id="demo",
            model_name="logistic_regression",
            auto_run=False,
        )

        self.assertEqual(status.universe_size, 3)
        self.assertEqual(status.tickers_failed, 0)
        mock_latest_prices.assert_not_called()
        mock_store.return_value.list_trades.assert_called_once_with("demo", limit=20, ticker=None)

    @patch("app.api.virtual_trader.get_user_watchlist")
    @patch("app.api.virtual_trader.list_live_virtual_trader_trades")
    @patch("app.api.virtual_trader.get_account_ledger_service")
    @patch("app.api.virtual_trader.get_live_virtual_trader_status")
    def test_consolidated_sync_is_read_only_and_combines_shared_state(
        self,
        mock_status,
        mock_ledger,
        mock_decisions,
        mock_watchlist,
    ) -> None:
        mock_status.return_value = LiveStatus(
            user_id="demo",
            model_name="auto_best",
            generated_at_utc="2026-07-13T09:00:00+00:00",
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
            universe_size=1,
            tickers_evaluated=0,
            tickers_failed=0,
            fallback_used_count=0,
            equity_curve=[],
        )
        mock_ledger.return_value.list_recent_trade_events.return_value = [
            {"action": "buy", "ticker": "VOO"}
        ]
        mock_decisions.return_value = {
            "trades": [],
            "contribution_application_history": [],
        }
        mock_watchlist.return_value = (["VOO", "AAPL"], False, None)

        payload = get_virtual_trader_live_sync(
            user_id="demo",
            recent_trade_limit=20,
            decision_limit=100,
        )

        self.assertEqual(payload.user_id, "demo")
        self.assertEqual(payload.watchlist, ["VOO", "AAPL"])
        self.assertFalse(payload.using_system_default_watchlist)
        self.assertEqual(payload.recent_trades[0]["ticker"], "VOO")
        self.assertEqual(payload.status.account.total_equity, 1000.0)
        mock_status.assert_called_once_with(
            user_id="demo",
            tickers=None,
            model_name="auto_best",
            auto_run=False,
        )
        mock_watchlist.assert_called_once_with(user_id="demo", market="US")


if __name__ == "__main__":
    unittest.main()
