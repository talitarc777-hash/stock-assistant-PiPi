"""Ensure model-quality evidence survives the typed API response."""

import unittest
from unittest.mock import patch

from app.api.models import model_accuracy


class ModelApiMetadataTests(unittest.TestCase):
    @patch("app.api.models.resolve_selected_model_name", return_value="linear_regression")
    @patch("app.api.models.load_model_accuracy_summary")
    def test_accuracy_response_exposes_stationary_validation_schema(
        self,
        mock_load,
        _mock_resolve,
    ) -> None:
        mock_load.return_value = {
            "ticker": "SPY",
            "period": "5y",
            "target_name": "target_5d_return",
            "model_name": "linear_regression",
            "latest_rolling_accuracy": 0.55,
            "rolling_accuracy": [],
            "metrics": {
                "generated_at_utc": "2026-07-14T00:00:00+00:00",
                "ticker": "SPY",
                "period": "5y",
                "target_name": "target_5d_return",
                "task_type": "regression",
                "model_name": "linear_regression",
                "row_count": 1000,
                "feature_count": 20,
                "time_series_splits": 5,
                "validation_method": "purged_walk_forward",
                "validation_note": "time ordered",
                "validation_scheme_version": 4,
                "validation_gap_rows": 5,
                "feature_schema_version": 2,
                "stationary_features": True,
                "benchmark_relative_target": True,
                "outperformance_economics_gate": {
                    "passed": False,
                    "reasons": ["negative_average_net_stock_return"],
                    "evaluation_rows": 1000,
                    "active_signal_count": 71,
                    "round_trip_cost_pct": 0.1,
                    "average_net_stock_return_pct": -0.14,
                    "profitable_non_overlapping_path_rate": 0.4,
                    "worst_path_drawdown_pct": -11.48,
                },
                "fold_sizes": [],
                "metrics": {
                    "mae": 2.0,
                    "rmse": 3.0,
                    "direction_accuracy": 0.55,
                    "absolute_error_80_pct": 3.5,
                },
            },
        }

        response = model_accuracy(
            ticker="SPY",
            period="5y",
            target_name="target_5d_return",
            model_name="linear_regression",
            window=20,
            user_id=None,
        )

        self.assertTrue(response.metrics_summary.stationary_features)
        self.assertEqual(response.metrics_summary.feature_schema_version, 2)
        self.assertEqual(response.metrics_summary.validation_scheme_version, 4)
        self.assertEqual(response.metrics_summary.metrics.absolute_error_80_pct, 3.5)
        self.assertTrue(response.metrics_summary.benchmark_relative_target)
        self.assertFalse(response.metrics_summary.outperformance_economics_gate.passed)
        self.assertAlmostEqual(
            response.metrics_summary.outperformance_economics_gate.average_net_stock_return_pct,
            -0.14,
        )


if __name__ == "__main__":
    unittest.main()
