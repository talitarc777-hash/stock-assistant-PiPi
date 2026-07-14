"""Tests for leakage-safe, cost-aware research targets."""

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.services.research_pipeline import _add_target_columns, build_feature_dataset
from app.services.outperformance_economics import evaluate_outperformance_economics


class ResearchTargetTests(unittest.TestCase):
    def test_features_at_decision_date_do_not_change_when_future_price_changes(self) -> None:
        dates = pd.date_range("2024-01-02", periods=300, freq="B")

        def price_frame(base: float) -> pd.DataFrame:
            close = base + np.arange(len(dates), dtype=float) * 0.1
            return pd.DataFrame(
                {
                    "date": dates,
                    "open": close - 0.1,
                    "high": close + 0.2,
                    "low": close - 0.2,
                    "close": close,
                    "adj_close": close,
                    "volume": np.full(len(dates), 1_000_000.0),
                }
            )

        ticker_before = price_frame(100.0)
        ticker_after = ticker_before.copy()
        ticker_after.loc[105, ["open", "high", "low", "close", "adj_close"]] += 25.0
        benchmark = price_frame(90.0)

        with patch(
            "app.services.research_pipeline.get_price_history",
            side_effect=[ticker_before, benchmark],
        ):
            before = build_feature_dataset("SPY", period="10y", benchmark="VOO", include_news_sentiment=False)
        with patch(
            "app.services.research_pipeline.get_price_history",
            side_effect=[ticker_after, benchmark],
        ):
            after = build_feature_dataset("SPY", period="10y", benchmark="VOO", include_news_sentiment=False)

        feature_columns = [
            column
            for column in before.select_dtypes(include=[np.number, "bool"]).columns
            if not column.startswith("target_")
        ]
        pd.testing.assert_series_equal(
            before.loc[100, feature_columns],
            after.loc[100, feature_columns],
            check_names=False,
        )
        self.assertNotEqual(
            before.loc[100, "target_5d_return"],
            after.loc[100, "target_5d_return"],
        )

    def test_outperformance_target_uses_forward_benchmark_horizon_and_cost(self) -> None:
        frame = pd.DataFrame(
            {
                "close": [100.0, 100.0, 100.0, 100.0, 100.0, 101.0, 99.0],
                "benchmark_return_5d_pct": [0.0, 0.0, 0.0, 0.0, 0.0, 0.95, -0.95],
            }
        )

        result = _add_target_columns(frame)

        self.assertAlmostEqual(result.loc[0, "target_5d_return"], 1.0)
        self.assertAlmostEqual(result.loc[0, "target_5d_excess_return"], 0.05)
        self.assertEqual(result.loc[0, "target_5d_outperform"], 0)
        self.assertAlmostEqual(result.loc[1, "target_5d_excess_return"], -0.05)
        self.assertEqual(result.loc[1, "target_5d_outperform"], 0)
        self.assertTrue(pd.isna(result.loc[2, "target_5d_outperform"]))

    def test_outperformance_economics_requires_absolute_net_profit(self) -> None:
        dates = pd.date_range("2025-01-01", periods=15, freq="B")
        evaluation = pd.DataFrame(
            {
                "prediction_date": dates,
                "predicted_value": [1] * 15,
            }
        )
        profitable_dataset = pd.DataFrame(
            {
                "date": dates,
                "target_5d_return": [1.0] * 15,
                "target_5d_excess_return": [0.5] * 15,
            }
        )
        losing_dataset = profitable_dataset.assign(target_5d_return=-0.5)

        passed = evaluate_outperformance_economics(
            evaluation,
            profitable_dataset,
            round_trip_cost_pct=0.1,
        )
        failed = evaluate_outperformance_economics(
            evaluation,
            losing_dataset,
            round_trip_cost_pct=0.1,
        )

        self.assertTrue(passed["passed"])
        self.assertFalse(failed["passed"])
        self.assertIn("negative_average_net_stock_return", failed["reasons"])

    def test_outperformance_economics_uses_prediction_time_regime_controls(self) -> None:
        dates = pd.date_range("2025-01-01", periods=20, freq="B")
        evaluation = pd.DataFrame(
            {
                "prediction_date": dates,
                "predicted_value": [1] * 20,
                "is_regime_trade_allowed": [True] * 15 + [False] * 5,
                "market_regime_position_multiplier": [0.5] * 15 + [0.0] * 5,
            }
        )
        dataset = pd.DataFrame(
            {
                "date": dates,
                "target_5d_return": [1.0] * 15 + [-20.0] * 5,
                "target_5d_excess_return": [0.5] * 15 + [0.5] * 5,
            }
        )

        result = evaluate_outperformance_economics(
            evaluation,
            dataset,
            round_trip_cost_pct=0.1,
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["active_signal_count"], 15)
        self.assertAlmostEqual(result["average_net_stock_return_pct"], 0.45)
        self.assertTrue(result["regime_filter_applied"])
        self.assertTrue(result["position_multiplier_applied"])


if __name__ == "__main__":
    unittest.main()
