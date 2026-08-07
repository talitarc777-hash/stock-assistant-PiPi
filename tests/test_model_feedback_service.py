"""Tests for model outcome feedback and promotion scoring."""

from __future__ import annotations

from pathlib import Path
import unittest
import uuid

import pandas as pd

from app.services.model_feedback_service import ModelFeedbackService


class ModelFeedbackServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = str(
            Path("data") / f"test_model_feedback_{uuid.uuid4().hex}.db"
        )
        Path("data").mkdir(parents=True, exist_ok=True)
        self.service = ModelFeedbackService(db_path=self.db_path)

    def tearDown(self) -> None:
        path = Path(self.db_path)
        if path.exists():
            try:
                path.unlink()
            except PermissionError:
                pass

    @staticmethod
    def _payload(
        *,
        date: str = "2026-01-02",
        model_version: str = "v1",
    ) -> dict:
        return {
            "timestamp": f"{date}T21:00:00+00:00",
            "user_id": "u1",
            "ticker": "AAPL",
            "action": "buy",
            "quantity": 100,
            "price": 100.0,
            "model_name": "random_forest",
            "confidence_score": 0.62,
            "metadata": {
                "price_date": date,
                "prediction_value": 2.0,
                "task_type": "regression",
                "model_period": "2y",
                "model_version": model_version,
                "model_ticker": "AAPL",
                "decision_source": "production_model",
                "context_score": 68.0,
                "context_label": "supportive",
                "context_factors": [
                    "recent news tone supports the setup",
                    "technical trend is constructive",
                ],
            },
        }

    @classmethod
    def _shadow_payload(cls, *, date: str = "2026-01-02") -> dict:
        payload = cls._payload(date=date)
        payload["metadata"]["benchmark_shadow"] = {
            "status": "available",
            "execution_enabled": False,
            "benchmark": "VOO",
            "model_name": "random_forest",
            "model_period": "10y",
            "prediction": 1,
            "outperform_probability": 0.8,
        }
        return payload

    @staticmethod
    def _history(symbol: str, _: str) -> pd.DataFrame:
        dates = pd.bdate_range("2026-01-02", periods=12)
        if symbol == "VOO":
            closes = [100.0 + index * 0.2 for index in range(len(dates))]
        else:
            closes = [100.0 + index * 1.2 for index in range(len(dates))]
        return pd.DataFrame({"date": dates, "close": closes})

    def test_record_is_idempotent_and_evaluates_five_day_outcome(self) -> None:
        payload = self._payload()

        self.assertTrue(self.service.record_decision(payload))
        self.assertFalse(self.service.record_decision(payload))

        result = self.service.evaluate_pending(price_loader=self._history)
        rows = self.service.list_feedback(status="evaluated")

        self.assertEqual(result["evaluated"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["direction_correct"], 1)
        self.assertEqual(rows[0]["profitable_after_cost"], 1)
        self.assertGreater(rows[0]["strategy_net_return_pct"], 0)
        self.assertGreater(rows[0]["outcome_score"], 0.5)

    def test_feedback_summary_is_reliability_weighted(self) -> None:
        self.service.record_decision(self._payload())
        self.service.evaluate_pending(price_loader=self._history)

        summary = self.service.get_model_summary(
            ticker="AAPL",
            model_period="2y",
            model_name="random_forest",
        )

        self.assertEqual(summary["sample_count"], 1)
        self.assertGreater(summary["raw_feedback_score"], 0.5)
        self.assertGreater(summary["feedback_score"], 0.5)
        self.assertLess(summary["feedback_score"], summary["raw_feedback_score"])

    def test_oldest_mature_feedback_is_not_starved_by_new_rows(self) -> None:
        self.assertTrue(
            self.service.record_decision(
                self._payload(date="2026-01-02", model_version="old")
            )
        )
        self.assertTrue(
            self.service.record_decision(
                self._payload(date="2026-01-08", model_version="new")
            )
        )

        def partial_history(symbol: str, _: str) -> pd.DataFrame:
            dates = pd.bdate_range("2026-01-02", periods=6)
            closes = [100.0 + index for index in range(len(dates))]
            if symbol == "VOO":
                closes = [100.0 + index * 0.2 for index in range(len(dates))]
            return pd.DataFrame({"date": dates, "close": closes})

        result = self.service.evaluate_pending(
            price_loader=partial_history,
            limit=1,
        )

        self.assertEqual(result["evaluated"], 1)
        self.assertEqual(
            self.service.list_feedback(status="evaluated")[0]["decision_date"],
            "2026-01-02",
        )
        self.assertEqual(
            self.service.list_feedback(status="pending")[0]["decision_date"],
            "2026-01-08",
        )

    def test_classifier_zero_is_scored_as_bearish(self) -> None:
        payload = self._payload()
        payload["metadata"]["task_type"] = "classification"
        payload["metadata"]["prediction_value"] = 0.0
        self.assertTrue(self.service.record_decision(payload))

        def falling_history(symbol: str, _: str) -> pd.DataFrame:
            dates = pd.bdate_range("2026-01-02", periods=7)
            closes = (
                [100.0, 98.0, 96.0, 94.0, 92.0, 90.0, 89.0]
                if symbol == "AAPL"
                else [100.0] * len(dates)
            )
            return pd.DataFrame({"date": dates, "close": closes})

        self.service.evaluate_pending(price_loader=falling_history)
        row = self.service.list_feedback(status="evaluated")[0]

        self.assertEqual(row["direction_correct"], 1)
        self.assertGreater(row["strategy_net_return_pct"], 0)
        self.assertGreater(row["outcome_score"], 0.5)

    def test_list_feedback_filters_model_period_and_name(self) -> None:
        first = self._payload(date="2026-01-02", model_version="v1")
        second = self._payload(date="2026-01-05", model_version="v2")
        second["model_name"] = "linear_regression"
        second["metadata"]["model_period"] = "5y"
        self.assertTrue(self.service.record_decision(first))
        self.assertTrue(self.service.record_decision(second))

        rows = self.service.list_feedback(
            model_period="5y",
            model_name="linear_regression",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["model_period"], "5y")
        self.assertEqual(rows[0]["model_name"], "linear_regression")

    def test_fallback_rule_is_not_recorded_as_model_feedback(self) -> None:
        payload = self._payload()
        payload["metadata"]["decision_source"] = "fallback_rule"

        self.assertFalse(self.service.record_decision(payload))
        self.assertEqual(self.service.list_feedback(), [])

    def test_saved_model_is_recorded_but_rule_fallback_is_not(self) -> None:
        payload = self._payload()
        payload["metadata"]["decision_source"] = "saved_model"

        self.assertTrue(self.service.record_decision(payload))
        self.assertEqual(self.service.list_feedback()[0]["decision_source"], "saved_model")

    def test_global_model_feedback_is_attributed_to_global_registry(self) -> None:
        payload = self._payload()
        payload["metadata"]["model_ticker"] = "GLOBAL"
        payload["metadata"]["decision_source"] = "shared_global_candidate"
        self.assertTrue(self.service.record_decision(payload))
        self.service.evaluate_pending(price_loader=self._history)

        summary = self.service.get_model_summary(
            ticker="GLOBAL",
            model_period="2y",
            model_name="random_forest",
        )

        self.assertEqual(summary["sample_count"], 1)

    def test_context_adjustment_learns_from_repeated_factor_outcomes(self) -> None:
        dates = ["2026-01-02", "2026-01-05", "2026-01-06"]
        for index, date in enumerate(dates):
            self.service.record_decision(
                self._payload(date=date, model_version=f"v{index}")
            )
        self.service.evaluate_pending(price_loader=self._history)

        learned = self.service.get_context_adjustment(
            ticker="AAPL",
            factors=["technical trend is constructive"],
        )

        self.assertGreater(learned["adjustment"], 0)
        self.assertEqual(
            learned["matched_factors"][0]["samples"],
            3,
        )

    def test_feedback_only_blends_after_minimum_samples(self) -> None:
        unchanged = self.service.blend_validation_with_feedback(
            validation_score=0.60,
            feedback_summary={
                "sample_count": 2,
                "feedback_score": 0.20,
            },
        )
        blended = self.service.blend_validation_with_feedback(
            validation_score=0.60,
            feedback_summary={
                "sample_count": 20,
                "feedback_score": 0.80,
            },
        )

        self.assertEqual(unchanged, 0.60)
        self.assertGreater(blended, 0.60)

    def test_shadow_feedback_is_idempotent_and_scores_benchmark_target(self) -> None:
        payload = self._shadow_payload()

        self.assertTrue(self.service.record_benchmark_shadow(payload))
        self.assertFalse(self.service.record_benchmark_shadow(payload))
        result = self.service.evaluate_pending(price_loader=self._history)
        rows = self.service.list_benchmark_shadow_feedback(status="evaluated")
        summary = self.service.get_benchmark_shadow_summary(
            ticker="AAPL",
            model_period="10y",
            model_name="random_forest",
        )

        self.assertEqual(result["shadow_evaluated"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["direction_correct"], 1)
        self.assertGreater(rows[0]["active_net_return_pct"], 0)
        self.assertEqual(summary["sample_count"], 1)
        self.assertEqual(summary["active_signal_count"], 1)
        self.assertEqual(summary["direction_accuracy"], 1.0)
        self.assertEqual(summary["pending_count"], 0)
        self.assertEqual(summary["total_observation_count"], 1)
        self.assertEqual(summary["latest_observation_status"], "evaluated")

    def test_shadow_feedback_stays_pending_without_future_fifth_row(self) -> None:
        self.service.record_benchmark_shadow(self._shadow_payload())

        def short_history(symbol: str, period: str) -> pd.DataFrame:
            return self._history(symbol, period).iloc[:5]

        result = self.service.evaluate_pending(price_loader=short_history)
        summary = self.service.get_benchmark_shadow_summary(ticker="AAPL", model_period="10y", model_name="random_forest")

        self.assertEqual(result["shadow_evaluated"], 0)
        self.assertEqual(result["shadow_pending"], 1)
        self.assertEqual(
            len(self.service.list_benchmark_shadow_feedback(status="pending")),
            1,
        )
        self.assertEqual(summary["sample_count"], 0)
        self.assertEqual(summary["pending_count"], 1)
        self.assertEqual(summary["latest_observation_status"], "pending")
        self.assertEqual(summary["next_pending_observation_date"], "2026-01-02")
        self.assertEqual(summary["estimated_next_maturity_date"], "2026-01-09")
        self.assertEqual(summary["maturity_horizon_trading_days"], 5)

    def test_shadow_listing_filters_exact_model_period_and_name(self) -> None:
        primary = self._shadow_payload(date="2026-01-02")
        other = self._shadow_payload(date="2026-01-05")
        other["metadata"]["benchmark_shadow"]["model_period"] = "5y"
        other["metadata"]["benchmark_shadow"]["model_name"] = "logistic_regression"
        self.assertTrue(self.service.record_benchmark_shadow(primary))
        self.assertTrue(self.service.record_benchmark_shadow(other))

        rows = self.service.list_benchmark_shadow_feedback(
            ticker="AAPL",
            model_period="10y",
            model_name="random_forest",
            limit=20,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["model_period"], "10y")
        self.assertEqual(rows[0]["model_name"], "random_forest")


if __name__ == "__main__":
    unittest.main()
