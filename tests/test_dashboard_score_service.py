from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import dashboard as dashboard_api
from app.services.dashboard_score_service import (
    DashboardScoreService,
    _default_classification_provider,
)
from app.services.hkex_security_metadata import HkexSecurityMetadata
from app.services.ticker_classification import TickerClassification


DEFAULT_HK_TICKERS = ["0005", "0700", "1810", "3690", "9988"]


class DashboardScoreServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path("data") / f"test_dashboard_scores_{uuid4().hex}.db"
        self.active = {"HK": list(DEFAULT_HK_TICKERS), "US": ["AAPL", "VOO"]}
        self.calls: list[tuple[str, str]] = []
        self.scores = {
            "0005": 60,
            "0700": 0,
            "1810": 85,
            "3690": 70,
            "9988": 40,
            "0388": 95,
            "AAPL": 80,
            "VOO": 75,
        }
        self.now = datetime(2026, 8, 13, 4, 0, tzinfo=UTC)

        def watchlist_provider(_user_id: str, market: str):
            return list(self.active[market]), False, None

        def score_provider(ticker: str, market: str, _period: str):
            self.calls.append((market, ticker))
            score = self.scores[ticker]
            return {
                "latest_close": float(int(ticker) if market == "HK" else 100),
                "score_breakdown": {
                    "trend_score": score,
                    "momentum_score": 0,
                    "confirmation_score": 0,
                    "risk_penalty": 0,
                    "total_score": score,
                },
                "label": "test",
            }

        def classification_provider(ticker: str, market: str):
            primary = "etf" if ticker == "VOO" else "stock"
            return TickerClassification(
                ticker=ticker,
                primary_ticker_class=primary,
                stock_subclass="unknown" if primary == "stock" else None,
                classification_source="test_metadata",
            )

        def model_status_provider(ticker: str, _market: str):
            return {
                "model_name": "linear_regression",
                "model_ticker": ticker,
                "model_period": "5y",
                "model_source": "production_model",
                "model_status": "production",
            }

        self.service = DashboardScoreService(
            db_path=self.db_path,
            watchlist_provider=watchlist_provider,
            score_provider=score_provider,
            classification_provider=classification_provider,
            model_status_provider=model_status_provider,
            now_provider=lambda: self.now,
            cache_max_age=timedelta(hours=1),
        )

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.db_path}{suffix}")
            if candidate.exists():
                candidate.unlink()

    def test_default_hk_universe_scores_five_and_sorts_descending(self) -> None:
        result = self.service.top_scores(
            user_id="demo-user",
            market="HK",
            asset_type="stock",
            limit=10,
        )

        self.assertEqual(
            self.calls,
            [("HK", ticker) for ticker in DEFAULT_HK_TICKERS],
        )
        self.assertEqual(result["count"], 5)
        self.assertEqual(
            [row["ticker"] for row in result["rows"]],
            ["1810", "3690", "0005", "9988", "0700"],
        )
        self.assertEqual(result["rows"][-1]["score_breakdown"]["total_score"], 0)
        self.assertEqual(result["diagnostics"]["expected_count"], 5)
        self.assertEqual(result["diagnostics"]["scored_count"], 5)
        self.assertEqual(result["diagnostics"]["skipped_count"], 0)

    def test_watchlist_expansion_invalidates_complete_old_cache(self) -> None:
        self.service.refresh(user_id="demo-user", market="HK")
        self.calls.clear()
        self.active["HK"].append("0388")

        result = self.service.top_scores(user_id="demo-user", market="HK", limit=10)

        self.assertTrue(result["refreshed"])
        self.assertEqual(result["diagnostics"]["expected_count"], 6)
        self.assertEqual(result["diagnostics"]["scored_count"], 6)
        self.assertEqual(result["rows"][0]["ticker"], "0388")
        self.assertIn(("HK", "0388"), self.calls)

    def test_cached_unknown_hk_classification_is_repaired_when_metadata_arrives(self) -> None:
        unknown_provider = lambda ticker, market: TickerClassification(
            ticker=ticker,
            primary_ticker_class="unknown",
            stock_subclass=None,
            classification_source="unknown",
        )
        self.service.classification_provider = unknown_provider
        self.service.refresh(user_id="demo-user", market="HK")

        self.service.classification_provider = lambda ticker, market: TickerClassification(
            ticker=ticker,
            primary_ticker_class="stock",
            stock_subclass="unknown",
            classification_source="market_data",
        )
        self.calls.clear()
        result = self.service.top_scores(user_id="demo-user", market="HK", limit=10)

        self.assertTrue(result["refreshed"])
        self.assertEqual({row["primary_ticker_class"] for row in result["rows"]}, {"stock"})

    def test_deactivated_ticker_is_kept_historically_but_not_ranked(self) -> None:
        self.service.refresh(user_id="demo-user", market="HK")
        self.active["HK"].remove("1810")

        rows = self.service.raw_scores(user_id="demo-user", market="HK")

        self.assertNotIn("1810", [row["ticker"] for row in rows])
        with closing(self.service._connect()) as connection:
            historical = connection.execute(
                "SELECT COUNT(*) FROM dashboard_ticker_scores WHERE ticker = '1810'"
            ).fetchone()[0]
        self.assertEqual(historical, 1)

    def test_model_status_is_ticker_specific_and_never_filters_scores(self) -> None:
        self.service.refresh(user_id="demo-user", market="HK")
        rows = self.service.raw_scores(user_id="demo-user", market="HK")

        self.assertEqual(len(rows), 5)
        for row in rows:
            self.assertEqual(row["model_ticker"], row["ticker"])
            self.assertEqual(row["score_source"], "technical_indicators")
            self.assertFalse(row["model_applied_to_score"])

    def test_failure_is_reported_without_deleting_last_valid_score(self) -> None:
        self.service.refresh(user_id="demo-user", market="HK")

        original_provider = self.service.score_provider

        def failing_provider(ticker: str, market: str, period: str):
            if ticker == "3690":
                raise RuntimeError("provider unavailable")
            return original_provider(ticker, market, period)

        self.service.score_provider = failing_provider
        diagnostic = self.service.refresh(user_id="demo-user", market="HK")

        self.assertEqual(diagnostic["skipped_count"], 1)
        self.assertEqual(diagnostic["skipped"][0]["ticker"], "3690")
        self.assertIn("provider unavailable", diagnostic["skipped"][0]["reason"])
        self.assertNotIn("3690", diagnostic["scored_tickers"])
        self.assertIn("3690", diagnostic["cached_tickers"])

    def test_us_asset_filter_and_ranking_are_unchanged(self) -> None:
        result = self.service.top_scores(user_id="demo-user", market="US", limit=10)

        self.assertEqual([row["ticker"] for row in result["rows"]], ["AAPL", "VOO"])
        stock = self.service.top_scores(
            user_id="demo-user",
            market="US",
            asset_type="stock",
            limit=10,
        )
        self.assertEqual([row["ticker"] for row in stock["rows"]], ["AAPL"])

    def test_api_top_scores_uses_service_and_selected_ticker_is_not_an_input(self) -> None:
        api = FastAPI()
        api.include_router(dashboard_api.router)
        with patch(
            "app.api.dashboard.get_dashboard_score_service",
            return_value=self.service,
        ):
            with TestClient(api) as client:
                response = client.get(
                    "/dashboard/top-scores",
                    params={
                        "user_id": "demo-user",
                        "market": "HK",
                        "asset_type": "stock",
                        "period": "5y",
                        "limit": 10,
                    },
                )
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["count"], 5)
        self.assertEqual(set(payload["diagnostics"]["expected_tickers"]), set(DEFAULT_HK_TICKERS))

    def test_official_hkex_category_classifies_arbitrary_hk_equity_as_stock(self) -> None:
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
            result = _default_classification_provider("1810", "HK")

        self.assertEqual(result.primary_ticker_class, "stock")
        self.assertEqual(result.classification_source, "market_data")


if __name__ == "__main__":
    unittest.main()
