"""Regression tests for period-invariant recent drawdown features."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from app.services.indicators import add_technical_indicators


class IndicatorDrawdownWindowTests(unittest.TestCase):
    def test_peak_older_than_252_rows_rolls_out_of_recent_drawdown(self) -> None:
        close = np.concatenate(([200.0], np.full(252, 100.0), [110.0]))
        frame = pd.DataFrame(
            {
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": np.full(len(close), 1_000_000.0),
            }
        )

        result = add_technical_indicators(frame)

        self.assertAlmostEqual(result.iloc[-1]["drawdown_from_peak_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
