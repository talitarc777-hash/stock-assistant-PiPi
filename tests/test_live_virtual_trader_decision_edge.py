"""Tests for the cost-aware HOLD band and reversal protection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from app.services.live_virtual_trader import (
    _assess_prediction_edge,
    _decision_outcome,
    _is_runtime_model_source_eligible,
    _is_opposite_action_reversal_blocked,
)


def _edge(prediction: float, confidence: float = 0.75) -> dict:
    return _assess_prediction_edge(
        predicted_value=prediction,
        task_type="regression",
        confidence_score=confidence,
        confidence_threshold=0.55,
        min_predicted_return_pct=0.0,
        estimated_transaction_cost_pct=0.20,
        uncertainty={
            "out_of_sample_error_pct": 1.0,
            "calibrated_abstention_threshold_pct": 0.8,
        },
    )


class LiveVirtualTraderDecisionEdgeTests(unittest.TestCase):
    def test_hold_and_skip_have_distinct_internal_semantics(self) -> None:
        self.assertEqual(_decision_outcome("no_action", "model_not_bullish"), "HOLD")
        self.assertEqual(_decision_outcome("hold", "holding_position"), "HOLD")
        self.assertEqual(
            _decision_outcome("no_action", "risk_or_cash_constraint"),
            "SKIP",
        )
        self.assertEqual(_decision_outcome("buy", "model_bullish_signal"), "BUY")

    def test_only_lifecycle_validated_sources_can_enter_runtime(self) -> None:
        self.assertTrue(_is_runtime_model_source_eligible("production_model"))
        self.assertTrue(_is_runtime_model_source_eligible("validated_candidate"))
        self.assertFalse(_is_runtime_model_source_eligible("requested_model"))
        self.assertFalse(_is_runtime_model_source_eligible("saved_model"))
        self.assertFalse(_is_runtime_model_source_eligible("trained_model"))

    def test_low_confidence_and_weak_edge_hold(self) -> None:
        low_confidence = _edge(3.0, confidence=0.40)
        weak_edge = _edge(0.50)
        self.assertFalse(low_confidence["bullish"])
        self.assertEqual(low_confidence["reason"], "confidence_below_threshold")
        self.assertFalse(weak_edge["bullish"])
        self.assertFalse(weak_edge["bearish"])

    def test_strong_positive_edge_buys_and_small_negative_noise_holds(self) -> None:
        positive = _edge(2.0)
        noise = _edge(-0.30)
        self.assertTrue(positive["bullish"])
        self.assertFalse(noise["bearish"])

    def test_meaningful_downside_sells(self) -> None:
        downside = _edge(-2.0)
        self.assertTrue(downside["bearish"])

    def test_opposite_action_is_blocked_inside_prediction_horizon(self) -> None:
        now = datetime(2026, 8, 19, tzinfo=UTC)
        previous = {
            "action": "buy",
            "timestamp": (now - timedelta(days=1)).isoformat(),
        }
        self.assertTrue(
            _is_opposite_action_reversal_blocked(
                latest_executed_trade=previous,
                action="sell",
                now_utc=now,
                horizon_trading_days=5,
            )
        )
        self.assertFalse(
            _is_opposite_action_reversal_blocked(
                latest_executed_trade=previous,
                action="sell",
                now_utc=now,
                horizon_trading_days=5,
                risk_override=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
