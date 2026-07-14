"""Contracts for one shared web/Discord benchmark evidence payload."""

import unittest
from unittest.mock import Mock, patch

from app.api.model_lifecycle import get_benchmark_shadow_feedback


class BenchmarkShadowApiTests(unittest.TestCase):
    @patch("app.api.model_lifecycle.get_model_lifecycle_service")
    @patch("app.api.model_lifecycle.get_model_feedback_service")
    def test_filters_rows_to_exact_model_and_includes_historical_evidence(
        self,
        mock_feedback_factory,
        mock_lifecycle_factory,
    ) -> None:
        feedback = Mock()
        feedback.list_benchmark_shadow_feedback.return_value = [
            {"ticker": "SPY", "model_period": "10y", "model_name": "random_forest"}
        ]
        mock_feedback_factory.return_value = feedback
        lifecycle = Mock()
        lifecycle.get_benchmark_forward_promotion_gate.return_value = {
            "sample_count": 0,
            "pending_count": 1,
            "passed": False,
        }
        lifecycle.list_registry.return_value = [
            {
                "ticker": "SPY",
                "period": "10y",
                "model_name": "random_forest",
                "status": "candidate",
                "is_validated": True,
                "is_stale": False,
                "validation_score": 0.972,
                "metrics_summary": {
                    "validation_gate_version": 8,
                    "walk_forward_quality_gate": {
                        "balanced_direction_accuracy": 0.897,
                        "worst_class_recall": 0.808,
                    },
                    "outperformance_economics_gate": {"passed": True},
                },
            }
        ]
        mock_lifecycle_factory.return_value = lifecycle

        result = get_benchmark_shadow_feedback(
            ticker="SPY",
            model_period="10y",
            model_name="random_forest",
            status=None,
            limit=20,
        )

        feedback.list_benchmark_shadow_feedback.assert_called_once_with(
            ticker="SPY",
            model_period="10y",
            model_name="random_forest",
            status=None,
            limit=20,
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["historical_evidence"]["validation_gate_version"], 8)
        self.assertAlmostEqual(
            result["historical_evidence"]["quality_gate"]["balanced_direction_accuracy"],
            0.897,
        )
        self.assertTrue(result["historical_evidence"]["economics_gate"]["passed"])


if __name__ == "__main__":
    unittest.main()
