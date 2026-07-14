"""Contracts for the shared market-regime policy."""

import unittest

from app.services.market_regime import assess_market_regime


class MarketRegimeTests(unittest.TestCase):
    def test_normal_regime_allows_full_position(self) -> None:
        result = assess_market_regime(
            {
                "benchmark_return_20d_pct": 2.0,
                "drawdown_from_peak_pct": -3.0,
                "rolling_volatility_20_pct": 18.0,
            }
        )
        self.assertEqual(result["level"], "normal")
        self.assertTrue(result["new_position_allowed"])
        self.assertEqual(result["position_size_multiplier"], 1.0)

    def test_caution_regime_halves_position(self) -> None:
        result = assess_market_regime({"benchmark_return_20d_pct": -3.0})
        self.assertEqual(result["level"], "caution")
        self.assertEqual(result["position_size_multiplier"], 0.5)

    def test_stress_regime_blocks_new_position(self) -> None:
        result = assess_market_regime({"rolling_volatility_20_pct": 50.0})
        self.assertEqual(result["level"], "stress")
        self.assertFalse(result["new_position_allowed"])
        self.assertIn("extreme_volatility", result["reasons"])


if __name__ == "__main__":
    unittest.main()
