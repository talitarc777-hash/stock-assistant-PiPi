"""Tests for prediction-specific regression confidence."""

import unittest

from app.services.live_virtual_trader import _regression_prediction_confidence


class LiveVirtualTraderPredictionConfidenceTests(unittest.TestCase):
    def test_large_signal_relative_to_error_has_higher_confidence(self) -> None:
        metrics = {"metrics": {"absolute_error_80_pct": 1.0, "rmse": 1.2}}
        weak = _regression_prediction_confidence(
            predicted_return_pct=0.2,
            metrics_summary=metrics,
            model_reliability=0.7,
        )
        strong = _regression_prediction_confidence(
            predicted_return_pct=3.0,
            metrics_summary=metrics,
            model_reliability=0.7,
        )
        self.assertGreater(strong["confidence_score"], weak["confidence_score"])
        self.assertEqual(strong["error_source"], "absolute_error_80_pct")

    def test_missing_error_does_not_reuse_reliability_as_confidence(self) -> None:
        result = _regression_prediction_confidence(
            predicted_return_pct=2.0,
            metrics_summary={"metrics": {}},
            model_reliability=0.9,
        )
        self.assertIsNone(result["confidence_score"])
        self.assertEqual(result["reason"], "out_of_sample_error_missing")

    def test_rmse_supports_legacy_saved_artifacts(self) -> None:
        result = _regression_prediction_confidence(
            predicted_return_pct=2.0,
            metrics_summary={"metrics": {"rmse": 1.0}},
            model_reliability=0.8,
        )
        self.assertIsNotNone(result["confidence_score"])
        self.assertEqual(result["error_source"], "rmse")


if __name__ == "__main__":
    unittest.main()
