"""Safety tests for benchmark-relative live shadow inference."""

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.services.live_virtual_trader import (
    _build_benchmark_shadow_prediction,
    collect_benchmark_shadow_observation,
)


class _PositiveModel:
    classes_ = np.array([0, 1])

    def predict(self, frame):
        return np.array([1])

    def predict_proba(self, frame):
        return np.array([[0.2, 0.8]])


def _validated_bundle() -> dict:
    return {
        "model": _PositiveModel(),
        "feature_names": ["return_1d"],
        "metrics": {
            "validation_scheme_version": 4,
            "feature_schema_version": 2,
            "stationary_features": True,
            "benchmark_relative_target": True,
            "metrics": {"accuracy": 0.97},
            "outperformance_economics_gate": {
                "passed": True,
                "regime_filter_applied": True,
                "position_multiplier_applied": True,
                "average_net_stock_return_pct": 0.14,
                "profitable_non_overlapping_path_rate": 0.6,
                "worst_path_drawdown_pct": -4.4,
            },
        },
    }


class BenchmarkShadowPredictionTests(unittest.TestCase):
    @patch("app.services.live_virtual_trader.get_model_feedback_service")
    @patch("app.services.live_virtual_trader._build_benchmark_shadow_prediction")
    @patch("app.services.live_virtual_trader.prepare_stationary_feature_dataset")
    @patch("app.services.live_virtual_trader.build_feature_dataset")
    def test_system_collector_records_no_action_without_user_account(
        self,
        mock_dataset,
        mock_stationary,
        mock_shadow,
        mock_feedback,
    ) -> None:
        frame = pd.DataFrame({
            "date": pd.to_datetime(["2026-07-13", "2026-07-14"]),
            "close": [620.0, 625.0],
            "return_1d": [0.0, 0.01],
        })
        mock_dataset.return_value = frame
        mock_stationary.return_value = frame
        mock_shadow.return_value = {
            "status": "available",
            "model_name": "random_forest",
            "model_period": "10y",
            "benchmark": "VOO",
            "prediction": 0,
            "execution_enabled": False,
        }
        mock_feedback.return_value.record_benchmark_shadow.return_value = True

        result = collect_benchmark_shadow_observation(ticker="SPY")

        self.assertTrue(result["recorded"])
        payload = mock_feedback.return_value.record_benchmark_shadow.call_args.args[0]
        self.assertEqual(payload["user_id"], "system-shadow-collector")
        self.assertEqual(payload["action"], "no_action")
        self.assertEqual(payload["quantity"], 0.0)

    @patch("app.services.live_virtual_trader.load_trained_model_bundle")
    def test_valid_candidate_is_observable_but_never_executes(self, mock_load) -> None:
        mock_load.return_value = _validated_bundle()

        result = _build_benchmark_shadow_prediction(
            ticker="SPY",
            latest_row=pd.Series({"return_1d": 0.01}),
            stationary_latest_row=pd.Series({"return_1d": 0.01}),
            benchmark="VOO",
        )

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["signal"], "outperform")
        self.assertAlmostEqual(result["outperform_probability"], 0.8)
        self.assertFalse(result["execution_enabled"])
        mock_load.assert_called_once_with(
            ticker="SPY",
            period="10y",
            target_name="target_5d_outperform",
            model_name="random_forest",
        )

    @patch("app.services.live_virtual_trader.load_trained_model_bundle")
    def test_incomplete_economics_provenance_is_rejected(self, mock_load) -> None:
        bundle = _validated_bundle()
        bundle["metrics"]["outperformance_economics_gate"]["regime_filter_applied"] = False
        mock_load.return_value = bundle

        result = _build_benchmark_shadow_prediction(
            ticker="SPY",
            latest_row=pd.Series({"return_1d": 0.01}),
            stationary_latest_row=pd.Series({"return_1d": 0.01}),
            benchmark="VOO",
        )

        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["execution_enabled"])

    @patch("app.services.live_virtual_trader.load_trained_model_bundle")
    def test_benchmark_itself_is_not_compared_to_itself(self, mock_load) -> None:
        result = _build_benchmark_shadow_prediction(
            ticker="VOO",
            latest_row=pd.Series({"return_1d": 0.01}),
            stationary_latest_row=pd.Series({"return_1d": 0.01}),
            benchmark="VOO",
        )

        self.assertEqual(result["status"], "not_applicable")
        mock_load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
