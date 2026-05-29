"""Tests for lightweight live virtual trader status reads."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.live_virtual_trader import get_live_virtual_trader_status


class LiveVirtualTraderStatusTests(unittest.TestCase):
    """Verify status refreshes do not perform full-universe market scans."""

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


if __name__ == "__main__":
    unittest.main()
