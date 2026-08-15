"""Ticker classification normalization and API-field tests."""

import unittest
from unittest.mock import patch

from app.models.account_ledger import VirtualHoldingResponse
from app.models.live_virtual_trader import LiveTraderDecisionResponse
from app.models.ticker_classification import ClassifiedTickerResponse
from app.services.hkex_security_metadata import HkexSecurityMetadata
from app.services.ticker_classification import (
    classify_ticker,
    classify_ticker_for_market,
    normalize_ticker_symbol,
)


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

    def test_hk_market_response_uses_official_hkex_metadata(self) -> None:
        metadata = HkexSecurityMetadata(
            stock_code="1810",
            security_name="XIAOMI-W",
            board_lot=200,
            category="Equity",
            subcategory="Equity Securities (Main Board)",
            ccass_admitted=True,
            trading_currency="HKD",
            expiry_date=None,
            source_as_of="2026-08-13",
            source_url="https://www.hkex.com.hk/",
        )
        with patch(
            "app.services.ticker_classification.get_hk_security_metadata",
            return_value=metadata,
        ):
            result = classify_ticker_for_market("1810", market="HK")
            holding = VirtualHoldingResponse(
                ticker="1810",
                market="HK",
                quantity=200,
                avg_entry_price=20.0,
                current_price=20.5,
                market_value=4100.0,
                unrealized_pnl=100.0,
            )
            decision = LiveTraderDecisionResponse(
                ticker="1810",
                market="HK",
                timestamp="2026-08-13T00:00:00+00:00",
                user_id="demo-user",
                action="no_action",
                quantity=0,
                price=20.5,
                model_name="auto_best",
                reason="test",
                threshold_summary="test",
                technical_state_summary="test",
                news_sentiment_summary="test",
                benchmark_strength_summary="test",
                action_summary="test",
                cash_after=1000,
                holdings_after=200,
                realized_pnl=0,
                unrealized_pnl=100,
                metadata={"sector": "Technology"},
            )

        self.assertEqual(result.primary_ticker_class, "stock")
        self.assertEqual(holding.primary_ticker_class, "stock")
        self.assertEqual(holding.stock_subclass, "unknown")
        self.assertEqual(holding.classification_source, "market_data")
        self.assertEqual(decision.primary_ticker_class, "stock")
        self.assertEqual(decision.stock_subclass, "technology")

    def test_default_hk_universe_does_not_render_as_unknown(self) -> None:
        metadata_by_ticker = {
            ticker: HkexSecurityMetadata(
                stock_code=ticker,
                security_name=ticker,
                board_lot=100,
                category="Equity",
                subcategory="Equity Securities (Main Board)",
                ccass_admitted=True,
                trading_currency="HKD",
                expiry_date=None,
                source_as_of="2026-08-13",
                source_url="https://www.hkex.com.hk/",
            )
            for ticker in ("0005", "0700", "1810", "3690", "9988")
        }
        with patch(
            "app.services.ticker_classification.get_hk_security_metadata",
            side_effect=lambda ticker: metadata_by_ticker[str(ticker).replace(".HK", "")],
        ):
            results = [
                classify_ticker_for_market(ticker, market="HK")
                for ticker in metadata_by_ticker
            ]

        self.assertEqual([item.primary_ticker_class for item in results], ["stock"] * 5)
        self.assertNotIn("unknown", [item.primary_ticker_class for item in results])


if __name__ == "__main__":
    unittest.main()
