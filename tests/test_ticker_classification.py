"""Ticker classification normalization and API-field tests."""

import unittest

from app.models.ticker_classification import ClassifiedTickerResponse
from app.services.ticker_classification import classify_ticker, normalize_ticker_symbol


class TickerClassificationTests(unittest.TestCase):
    def test_manual_override_and_local_universe_metadata(self) -> None:
        aapl = classify_ticker("aapl")
        spy = classify_ticker("spy")
        self.assertEqual(aapl.primary_ticker_class, "stock")
        self.assertEqual(aapl.stock_subclass, "technology")
        self.assertEqual(spy.primary_ticker_class, "etf")
        self.assertIsNone(spy.stock_subclass)

    def test_provider_metadata_has_priority_and_maps_sector(self) -> None:
        provider = classify_ticker(
            "AAPL",
            market_metadata={"quote_type": "EQUITY", "sector": "Healthcare"},
        )
        provider_etf = classify_ticker(
            "AAPL",
            market_metadata={"quote_type": "ETF", "sector": "Technology"},
        )
        self.assertEqual(provider.primary_ticker_class, "stock")
        self.assertEqual(provider.stock_subclass, "healthcare")
        self.assertEqual(provider.classification_source, "market_data")
        self.assertEqual(provider_etf.primary_ticker_class, "etf")
        self.assertIsNone(provider_etf.stock_subclass)

    def test_normalizes_hk_class_share_forex_and_crypto_symbols(self) -> None:
        self.assertEqual(normalize_ticker_symbol("700.hk"), "0700.HK")
        self.assertEqual(normalize_ticker_symbol("brk.b"), "BRK-B")
        self.assertEqual(normalize_ticker_symbol("eur/usd"), "EURUSD=X")
        self.assertEqual(normalize_ticker_symbol("btc/usd"), "BTC-USD")
        self.assertEqual(classify_ticker("700.hk").stock_subclass, "communication_services")
        self.assertEqual(classify_ticker("eur/usd").primary_ticker_class, "forex")
        self.assertEqual(classify_ticker("btc/usd").primary_ticker_class, "crypto")

    def test_supports_non_stock_classes_without_sector_badges(self) -> None:
        examples = {
            "^GSPC": "index",
            "WELL": "reit",
            "US10Y": "fixed_income",
            "XAUUSD=X": "commodity",
            "GC=F": "derivative",
            "USD": "cash",
        }
        for symbol, expected in examples.items():
            with self.subTest(symbol=symbol):
                result = classify_ticker(symbol)
                self.assertEqual(result.primary_ticker_class, expected)
                self.assertIsNone(result.stock_subclass)

    def test_unknown_is_safe_fallback(self) -> None:
        result = classify_ticker("UNLISTED123")
        self.assertEqual(result.primary_ticker_class, "unknown")
        self.assertIsNone(result.stock_subclass)
        self.assertEqual(result.classification_source, "unknown")

    def test_response_model_adds_backward_compatible_classification_fields(self) -> None:
        payload = ClassifiedTickerResponse(ticker="SPY").model_dump()
        self.assertEqual(payload["ticker"], "SPY")
        self.assertEqual(payload["primary_ticker_class"], "etf")
        self.assertIsNone(payload["stock_subclass"])
        self.assertEqual(payload["classification_source"], "local_metadata")


if __name__ == "__main__":
    unittest.main()
