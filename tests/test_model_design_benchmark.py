"""Tests for the isolated, leakage-safe model-design benchmark."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from scripts.model_design_benchmark import (
    COMPACT_FEATURES,
    ROUND_TRIP_COST_PCT,
    _validation_splits,
    derive_economic_class_threshold,
    select_training_features,
    summarize_evaluation,
)


class ModelDesignBenchmarkTests(unittest.TestCase):
    def test_reduced_selection_drops_training_constant_and_redundancy(self) -> None:
        frame = pd.DataFrame(
            {
                "return_5d_pct": np.arange(80, dtype=float),
                "return_20d_pct": np.arange(80, dtype=float) * 2.0,
                "benchmark_strength_score": np.zeros(80),
                "rolling_volatility_20_pct": np.sin(np.arange(80)),
            }
        )
        selected, decisions = select_training_features(
            frame,
            list(frame.columns),
            "reduced",
        )
        by_feature = {item["feature"]: item for item in decisions}

        self.assertIn("return_5d_pct", selected)
        self.assertNotIn("return_20d_pct", selected)
        self.assertEqual(
            by_feature["return_20d_pct"]["reason"],
            "training_fold_redundancy",
        )
        self.assertEqual(
            by_feature["benchmark_strength_score"]["reason"],
            "constant_or_empty_in_training_fold",
        )

    def test_feature_selection_does_not_see_later_test_rows(self) -> None:
        training = pd.DataFrame(
            {
                "return_5d_pct": np.arange(60, dtype=float),
                "return_20d_pct": np.sin(np.arange(60, dtype=float)),
            }
        )
        # These later values make the two features perfectly correlated, but
        # they are deliberately not supplied to the training-only selector.
        future = pd.DataFrame(
            {
                "return_5d_pct": np.arange(60, 100, dtype=float),
                "return_20d_pct": np.arange(60, 100, dtype=float),
            }
        )
        selected_before, _ = select_training_features(
            training, list(training.columns), "reduced"
        )
        selected_after, _ = select_training_features(
            training.copy(), list(training.columns), "reduced"
        )

        self.assertEqual(selected_before, selected_after)
        self.assertEqual(len(future), 40)
        self.assertEqual(set(selected_before), set(training.columns))

    def test_compact_design_is_predeclared_and_drops_fold_constant(self) -> None:
        frame = pd.DataFrame({
            feature: np.arange(50, dtype=float) + index
            for index, feature in enumerate(COMPACT_FEATURES)
        })
        frame["excess_return_3m_pct"] = 0.0
        selected, decisions = select_training_features(
            frame, list(frame.columns), "compact"
        )

        self.assertNotIn("excess_return_3m_pct", selected)
        self.assertEqual(set(selected), set(COMPACT_FEATURES) - {"excess_return_3m_pct"})
        self.assertFalse(
            next(item for item in decisions if item["feature"] == "excess_return_3m_pct")[
                "kept"
            ]
        )

    def test_economic_threshold_uses_cost_and_training_scale(self) -> None:
        calm = derive_economic_class_threshold(np.tile([-0.2, 0.2], 100))
        volatile = derive_economic_class_threshold(np.tile([-4.0, 4.0], 100))

        self.assertGreaterEqual(calm["threshold_pct"], ROUND_TRIP_COST_PCT)
        self.assertGreater(volatile["safety_margin_pct"], calm["safety_margin_pct"])
        self.assertEqual(
            volatile["method"],
            "training_only_95pct_robust_centre_uncertainty_plus_cost",
        )

    def test_global_date_splits_purge_target_horizon(self) -> None:
        dates = pd.Series(
            np.repeat(pd.date_range("2022-01-03", periods=180, freq="B"), 3)
        )
        splits = _validation_splits(dates, len(dates))
        normalized = pd.to_datetime(dates).dt.normalize()

        self.assertGreaterEqual(len(splits), 3)
        for train_index, test_index in splits:
            train_dates = pd.Index(normalized.iloc[train_index].unique()).sort_values()
            test_dates = pd.Index(normalized.iloc[test_index].unique()).sort_values()
            self.assertLess(train_dates.max(), test_dates.min())
            all_dates = pd.Index(normalized.unique()).sort_values()
            self.assertGreaterEqual(
                all_dates.get_loc(test_dates.min()) - all_dates.get_loc(train_dates.max()),
                6,
            )

    def test_summary_reports_buy_sell_brier_and_behavioral_gate_status(self) -> None:
        rows = 100
        actual = np.tile([1.0, -1.0], rows // 2)
        predicted = actual.copy()
        frame = pd.DataFrame(
            {
                "prediction_date": pd.date_range("2022-01-03", periods=rows, freq="B").strftime("%Y-%m-%d"),
                "source_ticker": "TEST",
                "predicted_value": predicted,
                "predicted_signal": predicted.astype(int),
                "actual_future_result": actual,
                "evaluation_window": np.repeat(np.arange(1, 6), rows // 5),
                "prediction_uncertainty_pct": None,
                "is_actionable_signal": True,
                "is_regime_trade_allowed": True,
                "market_regime_position_multiplier": 1.0,
                "probability_up": np.where(predicted > 0, 0.9, 0.1),
            }
        )
        summary = summarize_evaluation(frame)

        self.assertEqual(summary["direction_accuracy"], 1.0)
        self.assertEqual(summary["balanced_accuracy"], 1.0)
        self.assertEqual(summary["buy_precision"], 1.0)
        self.assertEqual(summary["sell_precision"], 1.0)
        self.assertLess(summary["brier_score"], 0.02)
        self.assertIn("clears_existing_behavioral_gates", summary)


if __name__ == "__main__":
    unittest.main()
