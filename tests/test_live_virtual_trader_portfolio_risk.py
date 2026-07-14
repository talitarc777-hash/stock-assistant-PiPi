"""Tests for portfolio-level live virtual trader safeguards."""

import unittest

from app.services.live_virtual_trader import _build_portfolio_risk_state


class LiveVirtualTraderPortfolioRiskTests(unittest.TestCase):
    def test_normal_account_keeps_full_position_sizing(self) -> None:
        state = _build_portfolio_risk_state(
            {"total_account_value": 1050.0, "net_deposits": 1000.0}
        )
        self.assertEqual(state["level"], "normal")
        self.assertTrue(state["buy_allowed"])
        self.assertEqual(state["position_size_multiplier"], 1.0)

    def test_caution_account_halves_new_position_size(self) -> None:
        state = _build_portfolio_risk_state(
            {"total_account_value": 900.0, "net_deposits": 1000.0}
        )
        self.assertEqual(state["level"], "caution")
        self.assertTrue(state["buy_allowed"])
        self.assertEqual(state["position_size_multiplier"], 0.5)

    def test_large_loss_pauses_new_buys(self) -> None:
        state = _build_portfolio_risk_state(
            {"total_account_value": 820.0, "net_deposits": 1000.0}
        )
        self.assertEqual(state["level"], "paused")
        self.assertFalse(state["buy_allowed"])

    def test_critical_loss_also_reduces_existing_positions(self) -> None:
        state = _build_portfolio_risk_state(
            {"total_account_value": 700.0, "net_deposits": 1000.0}
        )
        self.assertEqual(state["level"], "critical")
        self.assertFalse(state["buy_allowed"])
        self.assertTrue(state["reduce_positions"])


if __name__ == "__main__":
    unittest.main()
