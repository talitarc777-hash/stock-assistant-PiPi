"""Tests for market data cleaning compatibility across pandas/yfinance versions."""

from __future__ import annotations

import unittest

import pandas as pd

from app.services.market_data import _clean_ohlcv_dataframe


class MarketDataCleaningTests(unittest.TestCase):
    def test_clean_ohlcv_accepts_unnamed_datetime_index(self) -> None:
        raw_df = pd.DataFrame(
            {
                "Open": [100.0, 101.0],
                "High": [102.0, 103.0],
                "Low": [99.0, 100.0],
                "Close": [101.0, 102.0],
                "Adj Close": [101.0, 102.0],
                "Volume": [1000, 1200],
            },
            index=pd.DatetimeIndex(["2024-01-02", "2024-01-03"]),
        )

        clean_df = _clean_ohlcv_dataframe(raw_df, ticker="VOO")

        self.assertEqual(
            list(clean_df.columns),
            ["date", "open", "high", "low", "close", "adj_close", "volume"],
        )
        self.assertEqual(clean_df["date"].dt.strftime("%Y-%m-%d").tolist(), ["2024-01-02", "2024-01-03"])
        self.assertEqual(clean_df["close"].tolist(), [101.0, 102.0])


if __name__ == "__main__":
    unittest.main()
