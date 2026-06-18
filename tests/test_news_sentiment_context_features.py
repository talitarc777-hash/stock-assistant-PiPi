"""Tests for headline-derived news context features."""

from __future__ import annotations

import unittest

import pandas as pd

from app.services.news_sentiment import (
    LexiconSentimentScorer,
    _aggregate_daily_features,
    _score_articles,
)


class NewsSentimentContextFeatureTests(unittest.TestCase):
    def test_headline_topics_aggregate_into_recent_context_ratios(self) -> None:
        article_df = pd.DataFrame(
            [
                {
                    "article_date": "2026-06-15",
                    "title": "Analyst upgrades OpenClaw and raises price target after strong earnings beat",
                },
                {
                    "article_date": "2026-06-15",
                    "title": "Government regulatory probe weighs on social media buzz for OpenClaw",
                },
            ]
        )
        scored = _score_articles(article_df=article_df, scorer=LexiconSentimentScorer())
        features = _aggregate_daily_features(
            scored_article_df=scored,
            date_index=pd.Series(pd.date_range("2026-06-15", periods=2, freq="D")),
            mapped_ticker="CLAW",
        )

        latest = features.sort_values("date").iloc[-1]

        self.assertEqual(int(latest["article_count_recent_7d"]), 2)
        self.assertGreater(latest["political_risk_article_ratio_recent_7d"], 0)
        self.assertGreater(latest["public_interest_article_ratio_recent_7d"], 0)
        self.assertGreater(latest["analyst_positive_ratio_recent_7d"], 0)
        self.assertGreater(latest["earnings_positive_ratio_recent_7d"], 0)


if __name__ == "__main__":
    unittest.main()
