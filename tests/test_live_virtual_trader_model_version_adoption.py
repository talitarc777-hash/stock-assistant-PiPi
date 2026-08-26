"""Integration guard: the Virtual Trader records the selected active version."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from app.services.live_virtual_trader import run_live_virtual_trader_now


class _ZeroRegressor:
    def predict(self, _frame):
        return [0.0]


class LiveVirtualTraderModelVersionAdoptionTests(unittest.TestCase):
    def test_active_pointer_version_is_loaded_and_persisted(self) -> None:
        market_date = pd.Timestamp.now(tz="UTC").normalize()
        features = pd.DataFrame(
            [
                {
                    "date": market_date,
                    "close": 100.0,
                    "sma_50": 100.0,
                    "sma_200": 100.0,
                    "rsi_14": 50.0,
                    "macd_line": 0.0,
                    "macd_signal": 0.0,
                    "rolling_volatility_20_pct": 20.0,
                }
            ]
        )
        account = {
            "as_of": market_date.isoformat(),
            "cash": 10_000.0,
            "net_deposits": 10_000.0,
            "holdings": [],
            "holdings_value": 0.0,
            "total_account_value": 10_000.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "latest_prices": {"AAPL": 100.0},
        }
        ledger = MagicMock()
        ledger.list_events.return_value = []
        ledger.build_account_summary.return_value = account
        store = MagicMock()
        store.list_trades.return_value = []
        lifecycle = MagicMock()
        lifecycle.resolve_runtime_model_candidates.return_value = [
            {
                "ticker": "AAPL",
                "period": "2y",
                "model_name": "linear_regression",
                "source": "production_model",
                "validation_score": 0.64,
                "runtime_score": 0.64,
                "feedback_summary": {},
                "model_version": "active-v2",
                "model_role": "incumbent",
                "lifecycle_status": "active",
                "artifact_dir": "ignored-by-mock",
                "training_end_date": "2026-08-18T00:00:00+00:00",
            }
        ]
        lifecycle.resolve_shadow_model_candidates.return_value = []
        feedback = MagicMock()
        feedback.get_context_adjustment.return_value = {
            "adjustment": 0.0,
            "matched_factors": [],
        }
        feedback.calibrate_direction_probability.return_value = {
            "probability": 0.0,
            "raw_confidence": 0.0,
            "source": "raw_insufficient_calibration_evidence",
            "sample_count": 0,
        }
        snapshot = {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000.0,
            "price_timestamp": market_date.isoformat(),
            "pe_ratio": 25.0,
            "market_cap": 1_000_000_000_000.0,
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "company_name": "Apple Inc.",
        }
        explanation = {
            "technical_state_summary": "neutral",
            "news_sentiment_summary": "neutral",
            "benchmark_strength_summary": "neutral",
            "explanation": "test",
        }
        equity_curve = {
            "latest_total_equity": 10_000.0,
            "curve_last_point_timestamp": market_date.isoformat(),
            "points": [],
        }

        with (
            patch("app.services.live_virtual_trader.build_feature_dataset", return_value=features),
            patch("app.services.live_virtual_trader.prepare_stationary_feature_dataset", return_value=features),
            patch("app.services.live_virtual_trader.get_live_market_snapshot", return_value=snapshot),
            patch("app.services.live_virtual_trader.get_model_lifecycle_service", return_value=lifecycle),
            patch("app.services.live_virtual_trader.get_account_ledger_service", return_value=ledger),
            patch("app.services.live_virtual_trader.get_live_virtual_trader_store", return_value=store),
            patch("app.services.live_virtual_trader.get_model_feedback_service", return_value=feedback),
            patch("app.services.live_virtual_trader.load_trained_model_bundle", return_value={
                "model": _ZeroRegressor(),
                "feature_names": ["close"],
                "task_type": "regression",
                "model_name": "linear_regression",
                "metrics": {"stationary_features": True, "metrics": {"rmse": 1.0}},
            }) as load_bundle,
            patch("app.services.live_virtual_trader.score_from_indicators", return_value=SimpleNamespace(total_score=50)),
            patch("app.services.live_virtual_trader.build_prediction_explanation", return_value=explanation),
            patch("app.services.live_virtual_trader._assess_market_data_quality", return_value={"trade_safe": True, "status": "ready", "reasons": []}),
            patch("app.services.live_virtual_trader.assess_market_regime", return_value={"level": "normal", "position_size_multiplier": 1.0, "new_position_allowed": True, "reasons": []}),
            patch("app.services.live_virtual_trader._score_virtual_trader_context", return_value={"score": 50.0, "label": "cautious", "factors": [], "summary": "neutral", "missing_context": []}),
            patch("app.services.live_virtual_trader._build_benchmark_shadow_prediction", return_value={"status": "unavailable", "execution_enabled": False}),
            patch("app.services.live_virtual_trader.build_live_equity_curve", return_value=equity_curve),
        ):
            status = run_live_virtual_trader_now(
                user_id="demo",
                tickers=["AAPL"],
                model_name="auto_best",
            )

        decision = status.latest_decisions[0]
        self.assertEqual(decision["metadata"]["model_version"], "active-v2")
        self.assertEqual(decision["metadata"]["model_role"], "incumbent")
        self.assertTrue(decision["metadata"]["active_model_used"])
        self.assertIn("decision_reason_metadata", decision["metadata"])
        self.assertEqual(load_bundle.call_args.kwargs["artifact_dir"], "ignored-by-mock")
        feedback.record_decision.assert_called_once()
        recorded = feedback.record_decision.call_args.args[0]
        self.assertEqual(recorded["metadata"]["model_version"], "active-v2")


if __name__ == "__main__":
    unittest.main()
