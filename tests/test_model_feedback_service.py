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
                "model_period": "2y",
                "model_version": model_version,
                "decision_source": "production_model",
                "context_score": 68.0,
                "context_label": "supportive",
                "context_factors": [
                    "recent news tone supports the setup",
                    "technical trend is constructive",
                ],
            },
        }

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


if __name__ == "__main__":
    unittest.main()
