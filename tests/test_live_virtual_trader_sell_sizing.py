"""Tests for live virtual trader whole-share partial exit sizing."""

from __future__ import annotations

import unittest

from app.services.live_virtual_trader import _calculate_sell_quantity


class LiveVirtualTraderSellSizingTests(unittest.TestCase):
    def test_normal_sell_reduces_about_half_and_keeps_whole_shares(self) -> None:
        self.assertEqual(_calculate_sell_quantity(9, "model_bearish_signal"), 5)
        self.assertEqual(_calculate_sell_quantity(8, "take_profit"), 4)

    def test_stop_loss_closes_the_whole_position(self) -> None:
        self.assertEqual(_calculate_sell_quantity(9, "stop_loss"), 9)

    def test_single_share_normal_sell_can_close_the_position(self) -> None:
        self.assertEqual(_calculate_sell_quantity(1, "model_bearish_signal"), 1)


if __name__ == "__main__":
    unittest.main()
