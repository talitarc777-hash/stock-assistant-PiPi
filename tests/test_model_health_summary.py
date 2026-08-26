"""Tests for the compact model-health diagnostic."""

from __future__ import annotations

from pathlib import Path
import unittest
from uuid import uuid4

from app.services.live_virtual_trader import LiveVirtualTraderStore
from app.services.model_lifecycle_service import (
    ModelLifecycleService,
    VALIDATION_GATE_VERSION,
)


class ModelHealthSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path("data") / f"test_model_health_{uuid4().hex}.db"
        self.service = ModelLifecycleService(db_path=str(self.db_path))
        self.store = LiveVirtualTraderStore(db_path=str(self.db_path))

    def tearDown(self) -> None:
        if self.db_path.exists():
            try:
                self.db_path.unlink()
            except PermissionError:
                pass

    def test_health_separates_current_validation_fallback_hold_and_skip(self) -> None:
        gate = {"passed": True, "reasons": []}
        self.service._upsert_registry(  # pylint: disable=protected-access
            ticker="AAPL",
            period="2y",
            target_name="target_5d_return",
            model_name="ridge_regression",
            status="candidate",
            is_validated=True,
            validation_score=0.61,
            stale_after_days=30,
            retrain_type="test",
            metrics_summary={
                "validation_gate_version": VALIDATION_GATE_VERSION,
                "validation_scheme_version": 5,
                "validation_gap_rows": 5,
                "stationary_features": True,
                "feature_schema_version": 2,
                "walk_forward_quality_gate": gate,
                "historical_trading_quality_gate": gate,
            },
            notes=None,
            last_trained_at_utc="2026-08-20T00:00:00+00:00",
            last_evaluated_at_utc="2026-08-20T00:00:00+00:00",
        )
        base_payload = {
            "timestamp": "2026-08-20T00:00:00+00:00",
            "user_id": "health-test",
            "market": "US",
            "ticker": "AAPL",
            "action": "no_action",
            "quantity": 0.0,
            "price": 100.0,
            "model_name": "backup_rules",
            "confidence_score": 0.55,
            "threshold_summary": "test",
            "technical_state_summary": "test",
            "news_sentiment_summary": "test",
            "benchmark_strength_summary": "test",
            "action_summary": "test",
            "cash_after": 0.0,
            "holdings_after": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
        }
        self.store.append_trade(
            {
                **base_payload,
                "reason": "fallback_rule_neutral_hold",
                "metadata": {
                    "decision_source": "fallback_rule",
                    "decision_source_category": "safety_fallback",
                    "decision_outcome": "HOLD",
                    "fallback_reason": "fallback_rule_neutral_hold",
                },
            }
        )
        self.store.append_trade(
            {
                **base_payload,
                "timestamp": "2026-08-20T01:00:00+00:00",
                "reason": "risk_or_cash_constraint",
                "metadata": {
                    "decision_source": "fallback_rule",
                    "decision_source_category": "safety_fallback",
                    "decision_outcome": "SKIP",
                    "fallback_reason": "fallback_rule_bullish_trend_momentum",
                },
            }
        )

        health = self.service.get_model_health_summary("US")

        self.assertEqual(health["registry_provenance"]["currently_validated"], 1)
        self.assertEqual(health["runtime"]["fallback_usage_count"], 2)
        self.assertEqual(health["runtime"]["decision_outcomes"], {"SKIP": 1, "HOLD": 1})
        self.assertIn("hold_definition", health)
        self.assertIn("skip_definition", health)


if __name__ == "__main__":
    unittest.main()
