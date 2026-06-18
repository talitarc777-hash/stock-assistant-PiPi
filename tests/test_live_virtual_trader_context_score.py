"""Tests for virtual trader non-model context scoring."""

from __future__ import annotations

import unittest

import pandas as pd

from app.services.live_virtual_trader import _score_virtual_trader_context


class LiveVirtualTraderContextScoreTests(unittest.TestCase):
    def test_supportive_context_scores_above_buy_threshold(self) -> None:
        row = pd.Series(
            {
                "article_count_recent_7d": 5,
                "average_sentiment_recent_7d": 0.3,
                "positive_article_ratio_recent_7d": 0.7,
                "negative_article_ratio_recent_7d": 0.1,
                "political_risk_article_ratio_recent_7d": 0.0,
                "public_interest_article_ratio_recent_7d": 0.4,
                "analyst_positive_ratio_recent_7d": 0.3,
                "analyst_negative_ratio_recent_7d": 0.0,
                "earnings_positive_ratio_recent_7d": 0.3,
                "earnings_negative_ratio_recent_7d": 0.0,
                "benchmark_strength_score": 100,
                "close": 120.0,
                "sma_50": 110.0,
                "sma_200": 90.0,
                "rolling_volatility_20_pct": 18.0,
            }
        )
        snapshot = {"pe_ratio": 25.0, "market_cap": 50_000_000_000, "sector": "Technology"}

        score = _score_virtual_trader_context(latest_row=row, snapshot=snapshot)

        self.assertGreaterEqual(score["score"], 55.0)
        self.assertEqual(score["label"], "supportive")
        self.assertIn("recent news tone supports the setup", score["factors"])
        self.assertIn("headline analyst revisions are positive", score["factors"])
        self.assertIn("headline earnings tone is positive", score["factors"])

    def test_weak_context_scores_below_hold_threshold(self) -> None:
        row = pd.Series(
            {
                "article_count_recent_7d": 4,
                "average_sentiment_recent_7d": -0.4,
                "positive_article_ratio_recent_7d": 0.0,
                "negative_article_ratio_recent_7d": 0.75,
                "political_risk_article_ratio_recent_7d": 0.5,
                "public_interest_article_ratio_recent_7d": 0.4,
                "analyst_positive_ratio_recent_7d": 0.0,
                "analyst_negative_ratio_recent_7d": 0.4,
                "earnings_positive_ratio_recent_7d": 0.0,
                "earnings_negative_ratio_recent_7d": 0.4,
                "benchmark_strength_score": 0,
                "close": 70.0,
                "sma_50": 80.0,
                "sma_200": 100.0,
                "rolling_volatility_20_pct": 70.0,
            }
        )
        snapshot = {"pe_ratio": 120.0, "market_cap": 400_000_000}

        score = _score_virtual_trader_context(latest_row=row, snapshot=snapshot)

        self.assertLessEqual(score["score"], 35.0)
        self.assertEqual(score["label"], "weak")
        self.assertIn("recent news tone is a risk", score["factors"])
        self.assertIn("headline political/regulatory risk is elevated", score["factors"])
        self.assertIn("headline analyst revisions are negative", score["factors"])

    def test_external_context_can_lower_score(self) -> None:
        row = pd.Series(
            {
                "article_count_recent_7d": 2,
                "average_sentiment_recent_7d": 0.1,
                "positive_article_ratio_recent_7d": 0.5,
                "negative_article_ratio_recent_7d": 0.0,
                "benchmark_strength_score": 80,
                "close": 120.0,
                "sma_50": 110.0,
                "sma_200": 90.0,
                "rolling_volatility_20_pct": 20.0,
            }
        )
        snapshot = {"pe_ratio": 28.0, "market_cap": 50_000_000_000, "sector": "Technology"}
        external_context = {
            "social_sentiment_score": -0.4,
            "social_mention_count": 10,
            "social_engagement_score": 80,
            "analyst_revision_score": -0.5,
            "official_regulatory_risk_score": 60,
            "earnings_call_tone_score": -0.3,
            "missing_sources": [],
        }

        score = _score_virtual_trader_context(
            latest_row=row,
            snapshot=snapshot,
            external_context=external_context,
        )

        self.assertLess(score["score"], 55.0)
        self.assertIn("direct public-opinion feed is negative", score["factors"])
        self.assertIn("analyst consensus/revisions are a risk", score["factors"])
        self.assertIn("official regulatory filings show elevated event risk", score["factors"])
        self.assertIn("earnings-call transcript tone is negative", score["factors"])


if __name__ == "__main__":
    unittest.main()
