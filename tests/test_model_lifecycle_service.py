"""Tests for automatic model lifecycle registry behavior."""

from __future__ import annotations

import unittest
from pathlib import Path
import uuid

from app.services.model_lifecycle_service import ModelLifecycleService


class ModelLifecycleServiceTests(unittest.TestCase):
    """Verify registry promotion and runtime fallback hierarchy."""

    def setUp(self) -> None:
        self.db_path = str(Path("data") / f"test_model_lifecycle_{uuid.uuid4().hex}.db")
        Path("data").mkdir(parents=True, exist_ok=True)
        self.service = ModelLifecycleService(db_path=self.db_path)

    def tearDown(self) -> None:
        path = Path(self.db_path)
        if path.exists():
            try:
                path.unlink()
            except PermissionError:
                pass

    def test_resolve_runtime_model_candidates_uses_expected_priority(self) -> None:
        self.service._upsert_registry(  # pylint: disable=protected-access
            ticker="AAPL",
            period="5y",
            target_name="target_5d_updown",
            model_name="logistic_regression",
            status="production",
            is_validated=True,
            validation_score=0.61,
            stale_after_days=30,
            retrain_type="weekly_full",
            metrics_summary={},
            notes=None,
            last_trained_at_utc="2026-04-10T00:00:00+00:00",
            last_evaluated_at_utc="2026-04-10T00:00:00+00:00",
            last_promoted_at_utc="2026-04-10T00:00:00+00:00",
        )
        self.service._upsert_registry(  # pylint: disable=protected-access
            ticker="AAPL",
            period="5y",
            target_name="target_5d_updown",
            model_name="random_forest",
            status="candidate",
            is_validated=True,
            validation_score=0.60,
            stale_after_days=30,
            retrain_type="weekly_full",
            metrics_summary={},
            notes=None,
            last_trained_at_utc="2026-04-09T00:00:00+00:00",
            last_evaluated_at_utc="2026-04-09T00:00:00+00:00",
        )
        self.service._upsert_registry(  # pylint: disable=protected-access
            ticker="GLOBAL",
            period="5y",
            target_name="target_5d_updown",
            model_name="gradient_boosting",
            status="production",
            is_validated=True,
            validation_score=0.58,
            stale_after_days=30,
            retrain_type="monthly_deep",
            metrics_summary={},
            notes=None,
            last_trained_at_utc="2026-04-08T00:00:00+00:00",
            last_evaluated_at_utc="2026-04-08T00:00:00+00:00",
            last_promoted_at_utc="2026-04-08T00:00:00+00:00",
        )

        candidates = self.service.resolve_runtime_model_candidates(
            ticker="AAPL",
            period="5y",
            target_name="target_5d_updown",
            requested_model_name="linear_regression",
        )

        self.assertGreaterEqual(len(candidates), 4)
        self.assertEqual(candidates[0]["source"], "production_model")
        self.assertEqual(candidates[0]["model_name"], "logistic_regression")
        self.assertEqual(candidates[1]["source"], "validated_candidate")
        self.assertEqual(candidates[2]["source"], "shared_global_production")
        self.assertEqual(candidates[-1]["source"], "requested_model")

    def test_promote_candidate_archives_previous_production(self) -> None:
        self.service._upsert_registry(  # pylint: disable=protected-access
            ticker="VOO",
            period="5y",
            target_name="target_5d_updown",
            model_name="logistic_regression",
            status="production",
            is_validated=True,
            validation_score=0.54,
            stale_after_days=30,
            retrain_type="weekly_full",
            metrics_summary={},
            notes=None,
            last_trained_at_utc="2026-03-10T00:00:00+00:00",
            last_evaluated_at_utc="2026-03-10T00:00:00+00:00",
            last_promoted_at_utc="2026-03-10T00:00:00+00:00",
        )
        promoted = self.service._promote_candidate_if_eligible(  # pylint: disable=protected-access
            ticker="VOO",
            period="5y",
            target_name="target_5d_updown",
            model_name="random_forest",
            validation_score=0.57,
        )
        self.assertTrue(promoted)

        rows = self.service.list_registry(ticker="VOO", period="5y", target_name="target_5d_updown", limit=10)
        by_model = {row["model_name"]: row for row in rows}
        self.assertEqual(by_model["random_forest"]["status"], "production")
        self.assertEqual(by_model["logistic_regression"]["status"], "archived")

    def test_runtime_candidates_rank_validated_models_across_periods(self) -> None:
        for period, model_name, score in (
            ("2y", "linear_regression", 0.56),
            ("5y", "random_forest", 0.64),
            ("10y", "gradient_boosting", 0.60),
        ):
            self.service._upsert_registry(  # pylint: disable=protected-access
                ticker="AAPL",
                period=period,
                target_name="target_5d_return",
                model_name=model_name,
                status="production",
                is_validated=True,
                validation_score=score,
                stale_after_days=30,
                retrain_type="test",
                metrics_summary={},
                notes=None,
                last_trained_at_utc="2026-06-01T00:00:00+00:00",
                last_evaluated_at_utc="2026-06-01T00:00:00+00:00",
                last_promoted_at_utc="2026-06-01T00:00:00+00:00",
            )

        candidates = self.service.resolve_runtime_model_candidates(
            ticker="AAPL",
            period="2y",
            periods=("2y", "5y", "10y"),
            target_name="target_5d_return",
        )

        self.assertEqual(
            [(item["period"], item["model_name"]) for item in candidates],
            [
                ("5y", "random_forest"),
                ("10y", "gradient_boosting"),
                ("2y", "linear_regression"),
            ],
        )

    def test_trigger_workflow_uses_all_trading_periods(self) -> None:
        config = self.service._workflow_config("trigger_based")  # pylint: disable=protected-access
        self.assertEqual(tuple(config["periods"]), ("2y", "5y", "10y"))
        self.assertTrue(config["include_gradient"])


if __name__ == "__main__":
    unittest.main()
