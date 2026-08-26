"""Focused tests for the read-only model foundation audit."""

from __future__ import annotations

import unittest

import pandas as pd

from scripts.audit_model_quality import (
    _counterfactual_replay,
    _predictive_quality_comparison,
    _validation_funnels,
)


class ModelQualityAuditTests(unittest.TestCase):
    def test_replay_preserves_percentage_scale_and_reports_decision_funnel(self) -> None:
        rows = 40
        evaluation = pd.DataFrame(
            {
                "prediction_date": pd.date_range("2025-01-01", periods=rows, freq="B"),
                "predicted_value": [3.0] * 20 + [-3.0] * 20,
                "actual_future_result": [2.0] * 20 + [-2.0] * 20,
                "is_regime_trade_allowed": [True] * rows,
            }
        )
        metrics = {
            "metrics": {"rmse": 1.0},
            "target_return_scale": "percentage_points",
        }

        replay = _counterfactual_replay(evaluation, metrics, reliability=0.8)

        self.assertTrue(replay["available"])
        self.assertGreater(replay["decision_counts"].get("BUY", 0), 0)
        self.assertGreater(replay["decision_counts"].get("SELL", 0), 0)
        self.assertAlmostEqual(
            replay["predicted_return_pct_distribution"]["p99"], 3.0
        )
        self.assertIsNotNone(replay["confidence_distribution"]["p1"])
        self.assertIsNotNone(replay["confidence_distribution"]["p99"])
        self.assertGreater(replay["active_signal_direction_accuracy"], 0.9)
        self.assertEqual(replay["rapid_opposite_trade_transitions_inside_horizon"], 0)

    def test_predictive_comparison_uses_only_five_row_matured_baselines(self) -> None:
        evaluation = pd.DataFrame(
            {
                "predicted_value": [1.0, -1.0] * 20,
                "actual_future_result": [1.0, -1.0] * 20,
            }
        )

        result = _predictive_quality_comparison(evaluation)

        self.assertTrue(result["available"])
        self.assertEqual(result["model"]["direction_accuracy"], 1.0)
        self.assertEqual(result["model"]["balanced_direction_accuracy"], 1.0)
        self.assertIn("five-row lag", result["baseline_note"])

    def test_validation_funnel_is_cumulative_in_declared_gate_order(self) -> None:
        rows = [
            {
                "evaluation_readable": True,
                "validation_score": 0.60,
                "walk_forward_quality_passed": True,
                "historical_trading_quality_passed": True,
                "validation_provenance_current": True,
                "passed": True,
            },
            {
                "evaluation_readable": True,
                "validation_score": 0.60,
                "walk_forward_quality_passed": False,
                # This independent gate passes, but must not re-enter the
                # cumulative funnel after failing walk-forward quality.
                "historical_trading_quality_passed": True,
                "validation_provenance_current": True,
                "passed": False,
            },
            {
                "evaluation_readable": True,
                "validation_score": 0.40,
                "walk_forward_quality_passed": True,
                "historical_trading_quality_passed": True,
                "validation_provenance_current": True,
                "passed": False,
            },
        ]

        result = _validation_funnels(rows)
        cumulative = result["cumulative_gate_pass_counts"]

        self.assertEqual(cumulative["evaluation_readable"], 3)
        self.assertEqual(cumulative["plus_validation_score"], 2)
        self.assertEqual(cumulative["plus_walk_forward_quality"], 1)
        self.assertEqual(cumulative["plus_historical_trading_quality"], 1)
        self.assertEqual(cumulative["plus_current_validation_provenance"], 1)
        self.assertEqual(cumulative["all_gates_passed"], 1)


if __name__ == "__main__":
    unittest.main()
