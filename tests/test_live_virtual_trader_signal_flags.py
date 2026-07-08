"""Tests for live virtual trader prediction signal boundaries."""

from __future__ import annotations

import unittest

from app.services.live_virtual_trader import _derive_signal_flags


class LiveVirtualTraderSignalFlagsTests(unittest.TestCase):
    def test_zero_regression_prediction_is_neutral_not_bullish(self) -> None:
        bullish, bearish = _derive_signal_flags(0.0, "regression", 0.0)

        self.assertFalse(bullish)
        self.assertFalse(bearish)

    def test_positive_regression_prediction_is_bullish(self) -> None:
        bullish, bearish = _derive_signal_flags(0.01, "regression", 0.0)

        self.assertTrue(bullish)
        self.assertFalse(bearish)


if __name__ == "__main__":
    unittest.main()
