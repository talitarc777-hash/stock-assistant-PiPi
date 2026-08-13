"""Tests for market data cleaning compatibility across pandas/yfinance versions."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pandas as pd

from app.services.market_data import (
    _HISTORY_CACHE,
    _clean_ohlcv_dataframe,
    get_price_history,
)


@contextmanager
def _workspace_cache_directory():
    root = Path("data") / "test_market_history_cache"
    root.mkdir(parents=True, exist_ok=True)
    output_dir = root / uuid4().hex
    output_dir.mkdir()
    try:
        yield str(output_dir)
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


class MarketDataCleaningTests(unittest.TestCase):
    @staticmethod
    def _raw_history() -> pd.DataFrame:
        return pd.DataFrame(
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

    def test_last_valid_persistent_history_survives_provider_failure(self) -> None:
        with _workspace_cache_directory() as temp_dir:
            with patch(
                "app.services.market_data.get_settings",
                return_value=SimpleNamespace(market_history_cache_dir=temp_dir),
            ), patch(
                "app.services.market_data._default_download_daily",
                return_value=self._raw_history(),
            ):
                _HISTORY_CACHE.clear()
                first = get_price_history("0700", period="2y", market="HK")
                cache_file = Path(temp_dir) / "HK" / "0700.HK" / "2y.csv"
                self.assertTrue(cache_file.exists())

            with patch(
                "app.services.market_data.get_settings",
                return_value=SimpleNamespace(market_history_cache_dir=temp_dir),
            ), patch(
                "app.services.market_data._default_download_daily",
                side_effect=RuntimeError("temporary provider outage"),
            ):
                _HISTORY_CACHE.clear()
                cached = get_price_history("0700", period="2y", market="HK")

            pd.testing.assert_frame_equal(first, cached)


if __name__ == "__main__":
    unittest.main()
