"""Tests for the virtual trader simulation service."""

from __future__ import annotations

from pathlib import Path
import shutil
import unittest

import pandas as pd

from app.services.virtual_trader import simulate_virtual_trader


def _build_price_frame() -> pd.DataFrame:
    """Create a small two-month price series for simulation tests."""
    dates = pd.to_datetime(
        [
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-02-01",
            "2024-02-02",
            "2024-02-05",
        ]
    )
    closes = [100.0, 102.0, 101.0, 105.0, 95.0, 97.0]
    return pd.DataFrame({"date": dates, "close": closes})


def _build_benchmark_frame() -> pd.DataFrame:
    """Create a benchmark series aligned to the simulation dates."""
    dates = pd.to_datetime(
        [
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-02-01",
            "2024-02-02",
            "2024-02-05",
        ]
    )
    closes = [400.0, 402.0, 404.0, 406.0, 408.0, 410.0]
    return pd.DataFrame({"date": dates, "close": closes})


def _build_evaluation_frame() -> pd.DataFrame:
    """Create deterministic walk-forward signals with confidence values."""
    return pd.DataFrame(
        {
            "prediction_date": pd.to_datetime(["2024-01-02", "2024-02-01"]),
            "ticker": ["TEST", "TEST"],
            "predicted_value": [1, 0],
            "confidence_score": [0.80, 0.90],
            "actual_future_result": [1, 0],
            "hit_miss": ["hit", "hit"],
            "model_name": ["logistic_regression", "logistic_regression"],
            "target_name": ["target_5d_updown", "target_5d_updown"],
            "task_type": ["classification", "classification"],
            "evaluation_window": [1, 2],
        }
    )


class VirtualTraderTests(unittest.TestCase):
    """Verify beginner-friendly simulation behavior and saved outputs."""

    def test_simulate_virtual_trader_saves_expected_artifacts(self) -> None:
        temp_dir = Path("data/test_virtual_trader")
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

        try:
            result = simulate_virtual_trader(
                ticker="TEST",
                period="6mo",
                model_name="logistic_regression",
                price_df=_build_price_frame(),
                evaluation_df=_build_evaluation_frame(),
                benchmark_df=_build_benchmark_frame(),
                monthly_contribution_usd=1000.0,
                initial_cash=0.0,
                confidence_threshold=0.55,
                max_position_size_pct=0.50,
                stop_loss_pct=0.10,
                take_profit_pct=None,
                task_type="classification",
                output_dir=temp_dir,
            )

            self.assertEqual(result.ticker, "TEST")
            self.assertEqual(len(result.contribution_history), 2)
            self.assertGreaterEqual(len(result.trade_log), 2)
            self.assertGreater(len(result.equity_curve), 0)
            self.assertIn("benchmark_final_equity", result.summary)
            self.assertIn("max_drawdown_pct", result.summary)
            self.assertIn("annualized_volatility_pct", result.summary)
            self.assertIn("sharpe_ratio", result.summary)
            self.assertGreater(result.summary["risk_observation_count"], 0)
            self.assertIn("max_drawdown_pct", result.benchmark_comparison)
            self.assertLessEqual(result.summary["max_drawdown_pct"], 0)
            self.assertTrue(result.trade_log[0].action_summary)
            self.assertTrue(result.trade_log[0].threshold_summary)
            self.assertTrue(result.trade_log[0].explanation)
            self.assertTrue(result.trade_log[0].technical_state_summary)
            self.assertTrue(result.trade_log[0].news_sentiment_summary)
            self.assertTrue(result.trade_log[0].benchmark_strength_summary)
            self.assertTrue(Path(result.artifact.trade_log_path).exists())
            self.assertTrue(Path(result.artifact.equity_curve_path).exists())
            self.assertTrue(Path(result.artifact.contribution_history_path).exists())
            self.assertTrue(Path(result.artifact.summary_path).exists())
            self.assertTrue(Path(result.artifact.benchmark_comparison_path).exists())
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def test_simulate_virtual_trader_uses_monthly_schedule_when_provided(self) -> None:
        result = simulate_virtual_trader(
            ticker="TEST",
            period="6mo",
            model_name="logistic_regression",
            price_df=_build_price_frame(),
            evaluation_df=_build_evaluation_frame(),
            benchmark_df=_build_benchmark_frame(),
            contribution_schedule={"2024-01": 1200.0, "2024-02": 800.0},
            monthly_contribution_usd=1000.0,
            initial_cash=0.0,
            confidence_threshold=0.55,
            max_position_size_pct=0.50,
            stop_loss_pct=0.10,
            take_profit_pct=None,
            task_type="classification",
            output_dir=None,
        )

        self.assertEqual([item.amount for item in result.contribution_history], [1200.0, 800.0])
        self.assertIsNone(result.summary["monthly_contribution_usd"])
        self.assertEqual(result.summary["contribution_mode"], "custom_monthly_schedule")


if __name__ == "__main__":
    unittest.main()
