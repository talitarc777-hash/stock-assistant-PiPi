"""Tests for automatic model lifecycle registry behavior."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from pathlib import Path
import uuid

import pandas as pd

from app.services.model_lifecycle_service import ModelLifecycleService, VALIDATION_GATE_VERSION


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
            metrics_summary={"validation_gate_version": VALIDATION_GATE_VERSION},
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
            metrics_summary={"validation_gate_version": VALIDATION_GATE_VERSION},
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
            metrics_summary={"validation_gate_version": VALIDATION_GATE_VERSION},
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

    def test_outperformance_promotion_waits_for_forward_evidence(self) -> None:
        with patch.object(
            self.service.feedback_service,
            "get_benchmark_shadow_summary",
            return_value={
                "sample_count": 0,
                "active_signal_count": 0,
                "direction_accuracy": None,
                "average_active_net_return_pct": None,
                "active_profitable_rate": None,
            },
        ):
            promoted = self.service._promote_candidate_if_eligible(  # pylint: disable=protected-access
                ticker="SPY",
                period="10y",
                target_name="target_5d_outperform",
                model_name="random_forest",
                validation_score=0.97,
            )

        self.assertFalse(promoted)
        self.assertIsNone(
            self.service.get_production_model(
                ticker="SPY",
                period="10y",
                target_name="target_5d_outperform",
            )
        )

    def test_outperformance_promotion_accepts_profitable_forward_evidence(self) -> None:
        self.service._upsert_registry(  # pylint: disable=protected-access
            ticker="SPY",
            period="10y",
            target_name="target_5d_outperform",
            model_name="random_forest",
            status="candidate",
            is_validated=True,
            validation_score=0.97,
            stale_after_days=30,
            retrain_type="test",
            metrics_summary={"validation_gate_version": VALIDATION_GATE_VERSION},
            notes=None,
            last_trained_at_utc="2026-07-14T00:00:00+00:00",
            last_evaluated_at_utc="2026-07-14T00:00:00+00:00",
        )
        with patch.object(
            self.service.feedback_service,
            "get_benchmark_shadow_summary",
            return_value={
                "sample_count": 20,
                "active_signal_count": 8,
                "direction_accuracy": 0.65,
                "average_active_net_return_pct": 0.30,
                "active_profitable_rate": 0.625,
            },
        ):
            promoted = self.service._promote_candidate_if_eligible(  # pylint: disable=protected-access
                ticker="SPY",
                period="10y",
                target_name="target_5d_outperform",
                model_name="random_forest",
                validation_score=0.97,
            )

        self.assertTrue(promoted)
        production = self.service.get_production_model(
            ticker="SPY",
            period="10y",
            target_name="target_5d_outperform",
        )
        self.assertIsNotNone(production)
        self.assertEqual(production["model_name"], "random_forest")

    def test_refresh_demotes_legacy_outperformance_production_without_forward_evidence(self) -> None:
        self.service._upsert_registry(  # pylint: disable=protected-access
            ticker="SPY",
            period="10y",
            target_name="target_5d_outperform",
            model_name="random_forest",
            status="production",
            is_validated=True,
            validation_score=0.97,
            stale_after_days=30,
            retrain_type="legacy",
            metrics_summary={
                "validation_gate_version": VALIDATION_GATE_VERSION,
                "walk_forward_validation_score": 0.97,
                "walk_forward_quality_gate": {"passed": True},
                "historical_trading_quality_gate": {"passed": True},
            },
            notes=None,
            last_trained_at_utc="2026-07-14T00:00:00+00:00",
            last_evaluated_at_utc="2026-07-14T00:00:00+00:00",
            last_promoted_at_utc="2026-07-14T00:00:00+00:00",
        )
        with patch.object(
            self.service.feedback_service,
            "get_benchmark_shadow_summary",
            return_value={
                "sample_count": 0,
                "active_signal_count": 0,
                "direction_accuracy": None,
                "average_active_net_return_pct": None,
                "active_profitable_rate": None,
            },
        ):
            self.service.refresh_feedback_scores()

        row = self.service.list_registry(
            ticker="SPY",
            period="10y",
            target_name="target_5d_outperform",
            limit=1,
        )[0]
        self.assertEqual(row["status"], "candidate")

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
                metrics_summary={"validation_gate_version": VALIDATION_GATE_VERSION},
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

    def test_runtime_candidates_reject_legacy_validation_flags(self) -> None:
        self.service._upsert_registry(  # pylint: disable=protected-access
            ticker="AAPL",
            period="5y",
            target_name="target_5d_return",
            model_name="random_forest",
            status="production",
            is_validated=True,
            validation_score=0.70,
            stale_after_days=30,
            retrain_type="legacy",
            metrics_summary={
                "walk_forward_quality_gate": {"passed": True},
                "historical_trading_quality_gate": {"passed": True},
            },
            notes=None,
            last_trained_at_utc="2026-04-01T00:00:00+00:00",
            last_evaluated_at_utc="2026-04-01T00:00:00+00:00",
            last_promoted_at_utc="2026-04-01T00:00:00+00:00",
        )

        candidates = self.service.resolve_runtime_model_candidates(
            ticker="AAPL",
            period="5y",
            target_name="target_5d_return",
        )

        self.assertEqual(candidates, [])
        legacy_row = self.service.list_registry(
            ticker="AAPL",
            period="5y",
            target_name="target_5d_return",
            limit=1,
        )[0]
        self.assertTrue(legacy_row["stored_is_validated"])
        self.assertFalse(legacy_row["validation_evidence_current"])
        self.assertFalse(legacy_row["is_validated"])
        self.assertIsNone(
            self.service.get_production_model(
                ticker="AAPL",
                period="5y",
                target_name="target_5d_return",
            )
        )

    def test_mature_live_feedback_refreshes_registry_score_end_to_end(self) -> None:
        self.service._upsert_registry(  # pylint: disable=protected-access
            ticker="AAPL",
            period="2y",
            target_name="target_5d_return",
            model_name="random_forest",
            status="candidate",
            is_validated=True,
            validation_score=0.60,
            stale_after_days=30,
            retrain_type="test",
            metrics_summary={
                "validation_gate_version": VALIDATION_GATE_VERSION,
                "walk_forward_validation_score": 0.60,
                "walk_forward_quality_gate": {"passed": True},
                "historical_trading_quality_gate": {"passed": True},
            },
            notes=None,
            last_trained_at_utc="2026-01-01T00:00:00+00:00",
            last_evaluated_at_utc="2026-01-01T00:00:00+00:00",
        )
        dates = pd.bdate_range("2026-01-02", periods=20)
        stock_closes = [100.0 + index * 2.0 for index in range(len(dates))]
        benchmark_closes = [100.0 + index * 0.2 for index in range(len(dates))]
        for index, decision_date in enumerate(dates[:8]):
            date_text = decision_date.date().isoformat()
            self.assertTrue(
                self.service.feedback_service.record_decision(
                    {
                        "timestamp": f"{date_text}T21:00:00+00:00",
                        "user_id": "audit",
                        "ticker": "AAPL",
                        "action": "no_action",
                        "quantity": 0.0,
                        "price": stock_closes[index],
                        "model_name": "random_forest",
                        "confidence_score": 0.75,
                        "metadata": {
                            "price_date": date_text,
                            "prediction_value": 2.0,
                            "task_type": "regression",
                            "model_period": "2y",
                            "model_version": f"v{index}",
                            "model_ticker": "AAPL",
                            "decision_source": "validated_candidate",
                        },
                    }
                )
            )

        def price_loader(symbol: str, _: str) -> pd.DataFrame:
            closes = benchmark_closes if symbol == "VOO" else stock_closes
            return pd.DataFrame({"date": dates, "close": closes})

        settled = self.service.feedback_service.evaluate_pending(
            price_loader=price_loader,
            limit=20,
        )
        refreshed = self.service.refresh_feedback_scores()
        row = self.service.list_registry(
            ticker="AAPL",
            period="2y",
            target_name="target_5d_return",
            limit=1,
        )[0]

        self.assertEqual(settled["evaluated"], 8)
        self.assertGreaterEqual(refreshed["updated"], 1)
        self.assertEqual(
            row["metrics_summary"]["live_feedback"]["sample_count"],
            8,
        )
        self.assertGreater(row["validation_score"], 0.60)

    def test_trigger_workflow_uses_all_trading_periods(self) -> None:
        config = self.service._workflow_config("trigger_based")  # pylint: disable=protected-access
        self.assertEqual(tuple(config["periods"]), ("2y", "5y", "10y"))
        self.assertTrue(config["include_gradient"])

    def test_improvement_status_reports_both_markets_and_rejection_reasons(self) -> None:
        self.service._upsert_registry(  # pylint: disable=protected-access
            ticker="0700",
            period="2y",
            target_name="target_5d_return",
            model_name="ridge_regression",
            status="candidate",
            is_validated=False,
            validation_score=0.49,
            stale_after_days=30,
            retrain_type="daily_incremental",
            metrics_summary={
                "validation_gate_version": VALIDATION_GATE_VERSION,
                "walk_forward_quality_gate": {
                    "passed": False,
                    "reasons": ["balanced_accuracy_below_minimum"],
                },
                "historical_trading_quality_gate": {
                    "passed": False,
                    "reasons": ["negative_average_net_signal_return"],
                },
            },
            notes="test rejection evidence",
            last_trained_at_utc="2099-01-01T00:00:00+00:00",
            last_evaluated_at_utc="2099-01-01T00:00:00+00:00",
            market="HK",
        )

        payload = self.service.get_improvement_status()

        self.assertEqual(set(payload["markets"]), {"US", "HK"})
        self.assertEqual(payload["markets"]["HK"]["candidate_models"], 1)
        self.assertEqual(payload["markets"]["HK"]["validated_models"], 0)
        self.assertEqual(
            payload["markets"]["HK"]["top_rejection_reasons"],
            {
                "balanced_accuracy_below_minimum": 1,
                "negative_average_net_signal_return": 1,
            },
        )

    def test_quality_gate_rejects_majority_direction_shortcut(self) -> None:
        table = pd.DataFrame(
            {
                "predicted_value": [0.02] * 40,
                "actual_future_result": [0.01] * 30 + [-0.01] * 10,
                "evaluation_window": [1] * 20 + [2] * 20,
            }
        )

        result = self.service._walk_forward_quality_gate(table)  # pylint: disable=protected-access

        self.assertFalse(result["passed"])
        self.assertIn("no_edge_over_majority_baseline", result["reasons"])
        self.assertIn("one_sided_predictions", result["reasons"])
        self.assertIn("minority_event_recall_below_minimum", result["reasons"])

    def test_quality_gate_accepts_stable_edge_across_folds(self) -> None:
        actual = ([0.02, -0.01] * 40) + ([0.01, -0.02] * 40)
        predicted = actual.copy()
        predicted[7] *= -1
        predicted[26] *= -1
        table = pd.DataFrame(
            {
                "predicted_value": predicted,
                "actual_future_result": actual,
                "evaluation_window": [1] * 80 + [2] * 80,
            }
        )

        result = self.service._walk_forward_quality_gate(table)  # pylint: disable=protected-access

        self.assertTrue(result["passed"])
        self.assertGreater(result["direction_edge"], 0.01)
        self.assertGreaterEqual(result["worst_fold_accuracy"], 0.45)
        self.assertGreaterEqual(result["positive_edge_non_overlapping_path_rate"], 0.60)
        self.assertGreaterEqual(result["balanced_direction_accuracy"], 0.55)
        self.assertGreaterEqual(result["worst_class_recall"], 0.20)

    def test_quality_gate_scores_only_precalibrated_actionable_signals(self) -> None:
        actual: list[float] = []
        predicted: list[float] = []
        actionable: list[bool] = []
        for index in range(500):
            is_actionable = index % 5 == 0
            expected = 1.0 if (index // 5) % 2 == 0 else -1.0
            actual.append(expected)
            predicted.append(expected if is_actionable else -expected)
            actionable.append(is_actionable)
        table = pd.DataFrame(
            {
                "predicted_value": predicted,
                "actual_future_result": actual,
                "evaluation_window": [1] * 250 + [2] * 250,
                "is_actionable_signal": actionable,
            }
        )

        result = self.service._walk_forward_quality_gate(table)  # pylint: disable=protected-access

        self.assertTrue(result["passed"])
        self.assertTrue(result["actionable_filter_applied"])
        self.assertEqual(result["sample_count"], 100)
        self.assertEqual(result["effective_non_overlapping_sample_count"], 100)
        self.assertEqual(result["direction_accuracy"], 1.0)

    def test_quality_gate_applies_prediction_time_regime_filter(self) -> None:
        rows = []
        for index in range(500):
            allowed = index % 5 == 0
            actual = 1.0 if (index // 5) % 2 == 0 else -1.0
            rows.append(
                {
                    "predicted_value": actual if allowed else -actual,
                    "actual_future_result": actual,
                    "evaluation_window": 1 if index < 250 else 2,
                    "is_actionable_signal": True,
                    "is_regime_trade_allowed": allowed,
                }
            )

        result = self.service._walk_forward_quality_gate(  # pylint: disable=protected-access
            pd.DataFrame(rows)
        )

        self.assertTrue(result["passed"])
        self.assertTrue(result["regime_filter_applied"])
        self.assertEqual(result["sample_count"], 100)

    def test_pooled_quality_gate_requires_edge_across_tickers(self) -> None:
        frames = []
        for symbol in ("AAPL", "MSFT", "VOO"):
            actual = [1.0, -1.0] * 80
            frames.append(
                pd.DataFrame(
                    {
                        "predicted_value": actual,
                        "actual_future_result": actual,
                        "evaluation_window": [1] * 80 + [2] * 80,
                        "source_ticker": symbol,
                    }
                )
            )
        result = self.service._walk_forward_quality_gate(  # pylint: disable=protected-access
            pd.concat(frames, ignore_index=True)
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["pooled_ticker_pass_rate"], 1.0)

        weak = pd.concat(frames, ignore_index=True)
        weak.loc[weak["source_ticker"].isin(["AAPL", "MSFT"]), "predicted_value"] = 1.0
        weak_result = self.service._walk_forward_quality_gate(weak)  # pylint: disable=protected-access
        self.assertFalse(weak_result["passed"])
        self.assertIn("direction_edge_not_robust_across_tickers", weak_result["reasons"])

    def test_trading_gate_rejects_accurate_but_unprofitable_signals(self) -> None:
        returns = ([0.1] * 18) + ([-1.0] * 12) + ([-0.1] * 10)
        predictions = ([0.2] * 30) + ([-0.2] * 10)
        table = pd.DataFrame(
            {
                "predicted_value": predictions,
                "actual_future_result": returns,
            }
        )

        result = self.service._historical_trading_quality_gate(  # pylint: disable=protected-access
            table,
            "target_5d_return",
        )

        self.assertFalse(result["passed"])
        self.assertIn("negative_average_net_signal_return", result["reasons"])

    def test_trading_gate_accepts_profitable_diverse_signals(self) -> None:
        table = pd.DataFrame(
            {
                "predicted_value": ([0.5, -0.2] * 60),
                "actual_future_result": ([1.0, -0.5] * 60),
            }
        )

        result = self.service._historical_trading_quality_gate(  # pylint: disable=protected-access
            table,
            "target_5d_return",
        )

        self.assertTrue(result["passed"])
        self.assertGreater(result["average_active_return_pct_after_cost"], 0)
        self.assertGreater(result["cumulative_signal_return_pct_after_cost"], 0)
        self.assertEqual(result["non_overlapping_path_count"], 5)
        self.assertEqual(result["profitable_non_overlapping_path_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
