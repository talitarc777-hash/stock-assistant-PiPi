"""Technical indicator calculations for OHLCV price data."""

from __future__ import annotations

import numpy as np
import pandas as pd


class IndicatorInputError(Exception):
    """Raised when indicator inputs are missing required columns."""


def _validate_ohlcv_columns(df: pd.DataFrame) -> None:
    """
    Validate required input columns for indicator calculations.

    Required columns:
        open, high, low, close, volume
    """
    required_columns: list[str] = ["open", "high", "low", "close", "volume"]
    missing_columns: list[str] = [col for col in required_columns if col not in df.columns]

    if missing_columns:
        raise IndicatorInputError(
            f"Missing required OHLCV columns: {missing_columns}. "
            "Expected at least: open, high, low, close, volume."
        )


def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add common technical indicators to a copy of an OHLCV DataFrame.

    Input:
        DataFrame with OHLCV columns (open, high, low, close, volume).

    Output:
        DataFrame with original columns plus indicator columns.
    """
    _validate_ohlcv_columns(df)

    result: pd.DataFrame = df.copy()

    # Simple moving averages
    result["sma_20"] = result["close"].rolling(window=20, min_periods=20).mean()
    result["sma_50"] = result["close"].rolling(window=50, min_periods=50).mean()
    result["sma_200"] = result["close"].rolling(window=200, min_periods=200).mean()

    # Exponential moving averages
    result["ema_12"] = result["close"].ewm(span=12, adjust=False).mean()
    result["ema_26"] = result["close"].ewm(span=26, adjust=False).mean()

    # RSI (14), Wilder-style smoothing via EMA(alpha=1/period).
    rsi_period: int = 14
    close_delta = result["close"].diff()
    gain = close_delta.clip(lower=0.0)
    loss = -close_delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / rsi_period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / rsi_period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result["rsi_14"] = 100 - (100 / (1 + rs))

    # Handle edge cases where loss is zero (strong uptrend -> RSI = 100)
    result.loc[(avg_loss == 0) & (avg_gain > 0), "rsi_14"] = 100.0
    # If both gain and loss are zero (flat series), neutral RSI.
    result.loc[(avg_loss == 0) & (avg_gain == 0), "rsi_14"] = 50.0

    # MACD components
    result["macd_line"] = result["ema_12"] - result["ema_26"]
    result["macd_signal"] = result["macd_line"].ewm(span=9, adjust=False).mean()
    result["macd_histogram"] = result["macd_line"] - result["macd_signal"]

    # Average volume (20)
    result["avg_volume_20"] = result["volume"].rolling(window=20, min_periods=20).mean()

    # 52-week high distance (%), using 252 trading days rolling high.
    high_52w = result["high"].rolling(window=252, min_periods=1).max()
    result["distance_from_52w_high_pct"] = (
        (result["close"] - high_52w) / high_52w
    ) * 100

    # Rolling volatility: annualized std dev of daily returns over 20 days.
    daily_returns = result["close"].pct_change()
    result["rolling_volatility_20_pct"] = (
        daily_returns.rolling(window=20, min_periods=20).std() * np.sqrt(252) * 100
    )

    # Recent drawdown from the rolling 52-week peak close (%).  A full-history
    # cumulative peak made this feature depend on the requested lookback period
    # and could leave a recovered ticker in "stress" indefinitely.
    recent_peak = result["close"].rolling(window=252, min_periods=1).max()
    result["drawdown_from_peak_pct"] = ((result["close"] - recent_peak) / recent_peak) * 100

    return result
