"""Tests for the isolated relative/ranking/risk objective audit."""

from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from scripts.model_objective_benchmark import (
    RISK_ADVERSE_TARGET,
    RISK_VOLATILITY_TARGET,
    _classification_threshold,
    _calibrated_univariate_probability,
    _candidate_qualifies,
    add_forward_risk_targets,
    compute_market_holdout_boundaries,
    cross_sectional_ranking_summary,
    derive_regime_thresholds,
    deterministic_rule_signal,
    non_price_inventory,
    production_model_fingerprint,
    select_locked_candidate,
    split_development_and_locked,
)


class ModelObjectiveBenchmarkTests(unittest.TestCase):
    def test_forward_risk_targets_use_next_five_rows(self) -> None:
        close = [100.0, 90.0, 95.0, 80.0, 85.0, 110.0, 111.0]
        frame = pd.DataFrame({
            "date": pd.date_range("2024-01-02", periods=len(close), freq="B"),
            "close": close,
            "adj_close": close,
        })
        result = add_forward_risk_targets(frame)

        self.assertAlmostEqual(float(result.loc[0, RISK_ADVERSE_TARGET]), 20.0)
        self.assertGreater(float(result.loc[0, RISK_VOLATILITY_TARGET]), 0.0)
        self.assertTrue(pd.isna(result.loc[2, RISK_VOLATILITY_TARGET]))
        self.assertTrue(pd.isna(result.loc[2, RISK_ADVERSE_TARGET]))

    def test_holdout_boundary_has_five_date_purge(self) -> None:
        dates = pd.date_range("2020-01-02", periods=420, freq="B")
        base = pd.DataFrame({"date": dates})
        datasets = {
            ("US", "AAPL"): base,
            ("HK", "0700"): base,
        }
        boundaries = compute_market_holdout_boundaries(datasets, holdout_dates=100)
        development, locked = split_development_and_locked(base, boundaries["US"])

        self.assertEqual(pd.to_datetime(locked["date"]).nunique(), 100)
        self.assertEqual(len(boundaries["US"]["purged_dates"]), 5)
        self.assertLess(pd.to_datetime(development["date"]).max(), pd.to_datetime(locked["date"]).min())
        self.assertEqual(len(development) + len(locked) + 5, len(base))

    def test_event_threshold_uses_training_values_only(self) -> None:
        training = pd.Series(np.arange(1.0, 101.0))
        before = _classification_threshold("adverse_event", training)
        # Extreme future values are deliberately never passed to the function.
        future = pd.Series(np.full(50, 1_000_000.0))
        after = _classification_threshold("adverse_event", training.copy())

        self.assertEqual(before, after)
        self.assertEqual(before["quantile"], 0.85)
        self.assertEqual(len(future), 50)

    def test_regime_thresholds_are_training_derived(self) -> None:
        frame = pd.DataFrame({
            "benchmark_return_20d_pct": [-2.0, -1.0, 1.0, 4.0],
            "rolling_volatility_20_pct": [10.0, 20.0, 30.0, 40.0],
            "drawdown_from_peak_pct": [-20.0, -10.0, -5.0, 0.0],
        })
        thresholds = derive_regime_thresholds(frame)

        self.assertEqual(thresholds["bull_threshold_pct"], 0.0)
        self.assertEqual(thresholds["high_vol_threshold_pct"], 25.0)
        self.assertAlmostEqual(thresholds["stress_drawdown_threshold_pct"], -12.5)

    def test_existing_rule_score_is_reproduced_from_stationary_fields(self) -> None:
        strong = {
            "close_vs_sma_200_pct": 10,
            "close_vs_sma_50_pct": 5,
            "close_vs_sma_20_pct": 2,
            "rsi_14": 60,
            "macd_line_pct": 2,
            "macd_signal_pct": 1,
            "return_20d_pct": 5,
            "volume_vs_20d_avg": 1.5,
            "distance_from_52w_high_pct": -2,
            "rolling_volatility_20_pct": 20,
            "drawdown_from_peak_pct": -2,
        }
        weak = {key: 0 for key in strong}
        weak.update({
            "close_vs_sma_200_pct": -20,
            "close_vs_sma_50_pct": -10,
            "close_vs_sma_20_pct": -5,
            "rsi_14": 30,
            "distance_from_52w_high_pct": -30,
            "rolling_volatility_20_pct": 50,
            "drawdown_from_peak_pct": -25,
        })
        signals = deterministic_rule_signal(pd.DataFrame([strong, weak]))

        self.assertEqual(signals.tolist(), [1, 0])

    def test_perfect_cross_section_has_positive_ic_and_spread(self) -> None:
        rows = []
        for fold, date in enumerate(pd.date_range("2023-01-02", periods=80, freq="B")):
            for number, ticker in enumerate(("A", "B", "C", "D", "E"), start=1):
                rows.append({
                    "date": date,
                    "ticker": ticker,
                    "fold": min(5, fold // 16 + 1),
                    "actual": float(number),
                    "prediction": float(number),
                })
        summary = cross_sectional_ranking_summary(pd.DataFrame(rows))

        self.assertAlmostEqual(summary["spearman_ic"], 1.0)
        self.assertGreater(summary["after_cost_spread_pct"], 0.0)
        self.assertEqual(summary["positive_fold_rate"], 1.0)

    def test_candidate_selection_uses_development_summary(self) -> None:
        candidates = [
            {
                "name": "strong-ranking",
                "family": "cross_sectional_ranking",
                "task": "ranking",
                "model_name": "ridge_regression",
                "summary": {
                    "spearman_ic": 0.10,
                    "spearman_ic_ci_lower": 0.03,
                    "after_cost_spread_pct": 0.20,
                    "after_cost_spread_ci_lower": 0.05,
                    "positive_fold_rate": 0.8,
                },
            },
            {
                "name": "weak-ranking",
                "family": "cross_sectional_ranking",
                "task": "ranking",
                "model_name": "random_forest",
                "summary": {
                    "spearman_ic": -0.02,
                    "spearman_ic_ci_lower": -0.10,
                    "after_cost_spread_pct": -0.10,
                    "after_cost_spread_ci_lower": -0.20,
                    "positive_fold_rate": 0.2,
                },
            },
        ]
        selected = select_locked_candidate(candidates)

        self.assertEqual(selected["name"], "strong-ranking")
        self.assertTrue(selected["qualified"])
        self.assertNotIn("locked", select_locked_candidate.__code__.co_varnames)

    def test_risk_candidate_must_beat_best_simple_baseline(self) -> None:
        candidate = {
            "name": "risk",
            "family": "risk_filter",
            "task": "high_vol_event",
            "model_name": "random_forest",
            "summary": {
                "roc_auc_95pct_block_bootstrap_ci": [0.75, 0.82],
                "roc_auc_uplift_over_best_simple_baseline": -0.001,
                "pr_auc_uplift_over_best_simple_baseline": 0.01,
                "brier_improvement_over_best_simple_baseline": 0.01,
                "positive_skill_fold_rate": 1.0,
            },
        }
        qualified, reasons = _candidate_qualifies(candidate)

        self.assertFalse(qualified)
        self.assertIn("ROC-AUC does not beat the best simple proxy", reasons)

    def test_univariate_baseline_probability_is_training_calibrated(self) -> None:
        train_proxy = pd.Series(np.linspace(-3.0, 3.0, 200))
        labels = (train_proxy > 0.5).astype(int)
        test_proxy = pd.Series([-2.0, 0.0, 2.0])
        probability = _calibrated_univariate_probability(
            train_proxy, labels, test_proxy
        )

        self.assertTrue(np.all((probability >= 0) & (probability <= 1)))
        self.assertLess(probability[0], probability[1])
        self.assertLess(probability[1], probability[2])

    def test_inventory_rejects_snapshot_backfill_and_fingerprint_is_stable(self) -> None:
        self.assertTrue(all(not item["safe_for_this_experiment"] for item in non_price_inventory()))
        before = production_model_fingerprint(Path("tests"))
        after = production_model_fingerprint(Path("tests"))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
