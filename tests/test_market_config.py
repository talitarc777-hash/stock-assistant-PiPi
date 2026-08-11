"""Tests for market identity and Hong Kong exchange rules."""

from datetime import date
from decimal import Decimal
import unittest

import pandas as pd

from app.services.market_config import (
    MarketValidationError,
    hk_minimum_spread,
    is_valid_hk_price,
    resolve_security,
)
from app.services.market_data import get_price_history


class MarketConfigTests(unittest.TestCase):
    def test_hk_ticker_normalization(self) -> None:
        for raw, expected in (
            ("700", "0700.HK"),
            ("0700", "0700.HK"),
            ("0700.HK", "0700.HK"),
            ("5", "0005.HK"),
            ("9988", "9988.HK"),
        ):
            identity = resolve_security(raw, "HK")
            self.assertEqual(identity.provider_symbol, expected)
            self.assertEqual(identity.ticker, expected[:4])

    def test_invalid_hk_tickers_are_rejected(self) -> None:
        for raw in ("", "0", "10000", "AAPL", "0700.US", "../700"):
            with self.assertRaises(MarketValidationError):
                resolve_security(raw, "HK")

    def test_us_behavior_remains_unchanged(self) -> None:
        identity = resolve_security("voo", "US")
        self.assertEqual(identity.ticker, "VOO")
        self.assertEqual(identity.provider_symbol, "VOO")
        self.assertEqual(identity.currency, "USD")

    def test_phase_two_minimum_spreads(self) -> None:
        effective = date(2026, 8, 3)
        self.assertEqual(hk_minimum_spread("0.20", on_date=effective), Decimal("0.001"))
        self.assertEqual(hk_minimum_spread("0.50", on_date=effective), Decimal("0.005"))
        self.assertEqual(hk_minimum_spread("10", on_date=effective), Decimal("0.01"))
        self.assertEqual(hk_minimum_spread("20", on_date=effective), Decimal("0.02"))
        self.assertTrue(is_valid_hk_price("350.20", on_date=effective))
        self.assertFalse(is_valid_hk_price("350.10", on_date=effective))

    def test_hk_market_data_uses_yahoo_provider_symbol(self) -> None:
        requested: list[tuple[str, str]] = []

        def downloader(symbol: str, period: str) -> pd.DataFrame:
            requested.append((symbol, period))
            return pd.DataFrame(
                {
                    "Open": [100.0],
                    "High": [102.0],
                    "Low": [99.0],
                    "Close": [101.0],
                    "Adj Close": [101.0],
                    "Volume": [1000],
                },
                index=pd.DatetimeIndex(["2026-08-07"], name="Date"),
            )

        frame = get_price_history("700", "1mo", downloader, market="HK")

        self.assertEqual(requested, [("0700.HK", "1mo")])
        self.assertEqual(frame.iloc[-1]["close"], 101.0)


if __name__ == "__main__":
    unittest.main()
