"""Tests for saved model and virtual trader artifact readers."""

from __future__ import annotations

from pathlib import Path
import json
import shutil
import unittest

import pandas as pd

from app.services.model_results import (
    load_model_accuracy_summary,
    load_model_history,
    load_model_latest_prediction,
    load_virtual_trader_summary,
    load_virtual_trader_trades,
    list_compatible_saved_model_candidates,
)


class ModelResultsTests(unittest.TestCase):
    """Verify read-only loading of saved model and simulation artifacts."""

    def setUp(self) -> None:
        self.base_dir = Path("data/test_model_results")
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)

        model_dir = self.base_dir / "VOO" / "5y" / "target_5d_updown" / "logistic_regression"
        model_dir.mkdir(parents=True, exist_ok=True)
        evaluation_df = pd.DataFrame(
            [
                {
                    "prediction_date": "2024-01-02",
                    "ticker": "VOO",
                    "predicted_value": 1,
                    "confidence_score": 0.80,
                    "actual_future_result": 1,
                    "hit_miss": "hit",
                    "model_name": "logistic_regression",
                    "target_name": "target_5d_updown",
                    "task_type": "classification",
                    "evaluation_window": 1,
                    "technical_state_summary": "Constructive trend.",
                    "news_sentiment_summary": "Balanced news tone.",
                    "benchmark_strength_summary": "Relative strength favorable.",
                    "explanation": "Constructive setup with supportive relative strength.",
                },
                {
                    "prediction_date": "2024-01-03",
                    "ticker": "VOO",
                    "predicted_value": 0,
                    "confidence_score": 0.70,
                    "actual_future_result": 1,
                    "hit_miss": "miss",
                    "model_name": "logistic_regression",
                    "target_name": "target_5d_updown",
                    "task_type": "classification",
                    "evaluation_window": 1,
                    "technical_state_summary": "Mixed trend.",
                    "news_sentiment_summary": "Limited news.",
                    "benchmark_strength_summary": "Relative strength mixed.",
                    "explanation": "Signal turned cautious while feature backdrop stayed mixed.",
                },
            ]
        )
        evaluation_df.to_csv(model_dir / "evaluation_table.csv", index=False)
        metrics_payload = {
            "generated_at_utc": "2026-04-10T00:00:00+00:00",
            "ticker": "VOO",
            "period": "5y",
            "target_name": "target_5d_updown",
            "task_type": "classification",
            "model_name": "logistic_regression",
            "row_count": 100,
            "feature_count": 12,
            "time_series_splits": 3,
            "validation_method": "walk_forward_expanding_window",
            "validation_note": "Time order preserved.",
            "fold_sizes": [{"fold": 1, "train_rows": 30, "test_rows": 20}],
            "metrics": {
                "accuracy": 0.5,
                "precision": 1.0,
                "recall": 0.5,
                "f1": 0.67,
                "positive_rate_actual": 1.0,
                "positive_rate_predicted": 0.5,
            },
        }
        (model_dir / "metrics_summary.json").write_text(json.dumps(metrics_payload), encoding="utf-8")

        trader_dir = self.base_dir / "VOO" / "5y" / "virtual_trader" / "logistic_regression"
        trader_dir.mkdir(parents=True, exist_ok=True)
        summary_payload = {
            "generated_at_utc": "2026-04-10T00:00:00+00:00",
            "ticker": "VOO",
            "period": "5y",
            "model_name": "logistic_regression",
            "mode": "simulation_only_no_real_money_no_leverage",
            "task_type": "classification",
            "monthly_contribution_usd": 1000.0,
            "initial_cash": 0.0,
            "confidence_threshold": 0.55,
            "max_position_size_pct": 0.25,
            "stop_loss_pct": 0.10,
            "take_profit_pct": None,
            "total_contributions": 2000.0,
            "cash": 1500.0,
            "holdings": 2.0,
            "entry_price": 100.0,
            "exit_price": None,
            "realized_pnl": 25.0,
            "unrealized_pnl": 10.0,
            "final_equity": 2010.0,
            "return_on_contributions_pct": 0.5,
            "trade_count": 2,
            "benchmark_symbol": "VOO",
            "benchmark_final_equity": 2050.0,
            "outperformance_vs_benchmark_pct_points": -2.0,
        }
        benchmark_payload = {
            "benchmark": "VOO",
            "final_equity": 2050.0,
            "total_contributions": 2000.0,
            "return_on_contributions_pct": 2.5,
        }
        (trader_dir / "summary.json").write_text(json.dumps(summary_payload), encoding="utf-8")
        (trader_dir / "benchmark_comparison.json").write_text(json.dumps(benchmark_payload), encoding="utf-8")
        pd.DataFrame(
            [
                {
                    "date": "2024-01-02",
                    "cash": 1000.0,
                    "holdings_value": 0.0,
                    "total_equity": 1000.0,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 0.0,
                    "benchmark_equity": 1000.0,
                }
            ]
        ).to_csv(trader_dir / "equity_curve.csv", index=False)
        pd.DataFrame(
            [
                {
                    "timestamp": "2024-01-02",
                    "ticker": "VOO",
                    "action": "buy",
                    "price": 100.0,
                    "quantity": 2.0,
                    "cash_after": 800.0,
                    "holdings_after": 2.0,
                    "entry_price": 100.0,
                    "exit_price": None,
                    "position_size_value": 200.0,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 0.0,
                    "model_confidence": 0.8,
                    "trade_reason": "model_bullish_signal",
                    "threshold_summary": "Thresholds: confidence 80% >= required threshold 55%.",
                    "action_summary": "The simulator opened a position in response to a constructive model signal.",
                    "technical_state_summary": "Constructive trend.",
                    "news_sentiment_summary": "Balanced news tone.",
                    "benchmark_strength_summary": "Relative strength favorable.",
                    "prediction_explanation": "Constructive setup.",
                    "explanation": "The simulator opened a position in response to a constructive model signal.",
                }
            ]
        ).to_csv(trader_dir / "trade_log.csv", index=False)
        pd.DataFrame(
            [{"date": "2024-01-02", "amount": 1000.0, "cumulative_contributions": 1000.0}]
        ).to_csv(trader_dir / "monthly_contributions.csv", index=False)

    def tearDown(self) -> None:
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)

    def test_saved_model_scan_uses_ticker_parent_not_period(self) -> None:
        (self.base_dir / "VOO" / "5y" / "target_5d_updown" / "logistic_regression" / "model.pkl").touch()
        candidates = list_compatible_saved_model_candidates(
            ticker="VOO",
            period="5y",
            target_name="target_5d_updown",
            base_dir=self.base_dir,
        )

        self.assertEqual(candidates[0]["ticker"], "VOO")
        self.assertEqual(candidates[0]["model_name"], "logistic_regression")

    def test_load_model_latest_prediction(self) -> None:

        payload = load_model_latest_prediction(
            ticker="VOO",
            period="5y",
            target_name="target_5d_updown",
            model_name="logistic_regression",
            base_dir=self.base_dir,
        )
        self.assertEqual(payload["latest"]["prediction_date"], "2024-01-03")

    def test_load_model_history_and_accuracy(self) -> None:
        history_payload = load_model_history(
            ticker="VOO",
            period="5y",
            target_name="target_5d_updown",
            model_name="logistic_regression",
            base_dir=self.base_dir,
        )
        accuracy_payload = load_model_accuracy_summary(
            ticker="VOO",
            period="5y",
            target_name="target_5d_updown",
            model_name="logistic_regression",
            base_dir=self.base_dir,
        )
        self.assertEqual(history_payload["count"], 2)
        self.assertEqual(len(history_payload["rolling_accuracy"]), 2)
        self.assertIsNotNone(accuracy_payload["latest_rolling_accuracy"])

    def test_load_virtual_trader_artifacts(self) -> None:
        summary_payload = load_virtual_trader_summary(
            ticker="VOO",
            period="5y",
            model_name="logistic_regression",
            base_dir=self.base_dir,
        )
        trades_payload = load_virtual_trader_trades(
            ticker="VOO",
            period="5y",
            model_name="logistic_regression",
            base_dir=self.base_dir,
        )
        self.assertEqual(summary_payload["summary"]["ticker"], "VOO")
        self.assertEqual(trades_payload["trade_count"], 1)


if __name__ == "__main__":
    unittest.main()
