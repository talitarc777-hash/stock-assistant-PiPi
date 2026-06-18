"""Tests for optional external market context helpers."""

from __future__ import annotations

import unittest

import pandas as pd

from app.services.external_market_context_service import _latest_recommendation_score


class ExternalMarketContextServiceTests(unittest.TestCase):
    def test_latest_recommendation_score_prefers_buy_side_consensus(self) -> None:
        row = pd.Series(
            {
                "strongBuy": 5,
                "buy": 3,
                "hold": 2,
                "sell": 0,
                "strongSell": 0,
            }
        )

        score = _latest_recommendation_score(row)

        self.assertIsNotNone(score)
        self.assertGreater(score, 0)

    def test_latest_recommendation_score_detects_sell_side_consensus(self) -> None:
        row = pd.Series(
            {
                "strongBuy": 0,
                "buy": 1,
                "hold": 2,
                "sell": 3,
                "strongSell": 4,
            }
        )

        score = _latest_recommendation_score(row)

        self.assertIsNotNone(score)
        self.assertLess(score, 0)


if __name__ == "__main__":
    unittest.main()
