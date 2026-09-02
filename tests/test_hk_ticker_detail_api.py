"""Market-routing coverage for Dashboard and Virtual Trader ticker details."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from app.api.analyze import analyze_ticker
from app.api.forecast import forecast_ticker


def _price_frame() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=280)
    closes = [100.0 + index * 0.1 for index in range(len(dates))]
    return pd.DataFrame(
        {
            "date": dates,
            "open": [value - 0.2 for value in closes],
            "high": [value + 0.5 for value in closes],
            "low": [value - 0.5 for value in closes],
            "close": closes,
            "adj_close": closes,
            "volume": [1_000_000.0] * len(dates),
        }
    )


class HkTickerDetailApiTests(unittest.TestCase):
    @patch("app.models.ticker_classification.classify_ticker_for_market")
    @patch("app.api.analyze.get_security_profile")
    @patch("app.api.analyze.get_price_history")
    def test_analyze_uses_hk_identity_and_default_benchmark(
        self,
        history_mock,
        profile_mock,
        classification_mock,
    ) -> None:
        history_mock.return_value = _price_frame()
        profile_mock.return_value = {
            "ticker_name": "TENCENT",
            "company_name": "Tencent Holdings Limited",
            "security_name": "TENCENT",
        }
        classification_mock.return_value = SimpleNamespace(
            ticker="0700",
            primary_ticker_class="stock",
            stock_subclass="communication_services",
            classification_source="test",
        )

        response = analyze_ticker(
            ticker="700",
            period="2y",
            benchmark=None,
            market="HK",
        )

        self.assertEqual(response.ticker, "0700")
        self.assertEqual(response.market, "HK")
        self.assertEqual(response.provider_symbol, "0700.HK")
        self.assertEqual(response.currency, "HKD")
        self.assertEqual(response.ticker_name, "TENCENT")
        self.assertEqual(response.benchmark_relative.benchmark, "2800")
        self.assertEqual(history_mock.call_args_list[0].kwargs["ticker"], "2800")
        self.assertEqual(history_mock.call_args_list[0].kwargs["market"], "HK")
        self.assertEqual(history_mock.call_args_list[1].kwargs["ticker"], "0700")
        self.assertEqual(history_mock.call_args_list[1].kwargs["market"], "HK")

    @patch("app.api.forecast.save_forecast_snapshot")
    @patch("app.api.forecast.get_price_history")
    def test_forecast_uses_hk_market_and_collision_safe_symbol(
        self,
        history_mock,
        save_mock,
    ) -> None:
        history_mock.return_value = _price_frame()

        response = forecast_ticker(ticker="700", period="2y", market="HK")

        self.assertEqual(response.ticker, "0700")
        self.assertEqual(response.market, "HK")
        self.assertEqual(response.provider_symbol, "0700.HK")
        self.assertEqual(response.currency_symbol, "HK$")
        self.assertEqual(history_mock.call_args.kwargs["ticker"], "0700")
        self.assertEqual(history_mock.call_args.kwargs["market"], "HK")
        self.assertEqual(save_mock.call_args.kwargs["ticker"], "0700.HK")


if __name__ == "__main__":
    unittest.main()
