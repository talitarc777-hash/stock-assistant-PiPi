"""Deterministic, explainable stock scoring engine."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


class ScoringInputError(Exception):
    """Raised when scoring input data is missing required fields."""


@dataclass(frozen=True)
class ScoreBreakdown:
    """Structured score output used by API responses."""

    trend_score: int
    momentum_score: int
    confirmation_score: int
    risk_penalty: int
    total_score: int
    label: str
    action_summary: str
    explanations: list[str]


def _validate_columns(df: pd.DataFrame) -> None:
    """Validate all columns required by the scoring rules."""
    required_columns: list[str] = [
        "close",
        "sma_20",
        "sma_50",
        "sma_200",
        "rsi_14",
        "macd_line",
        "macd_signal",
        "avg_volume_20",
        "volume",
        "distance_from_52w_high_pct",
        "rolling_volatility_20_pct",
        "drawdown_from_peak_pct",
    ]
    missing: list[str] = [column for column in required_columns if column not in df.columns]
    if missing:
        raise ScoringInputError(f"Missing required indicator columns: {missing}")


def _get_label(total_score: int) -> str:
    """Map score into the requested label buckets."""
    if total_score >= 80:
        return "strong watchlist candidate"
    if total_score >= 65:
        return "watch closely"
    if total_score >= 50:
        return "neutral"
    return "avoid for now"


def _get_action_summary(
    total_score: int,
    risk_penalty: int,
    is_overextended: bool,
) -> str:
    """
    Build one deterministic action phrase from the requested options.

    Options:
        - accumulate on pullbacks
        - hold
        - avoid chasing
        - reduce risk watch
    """
    if risk_penalty <= -10 or total_score < 50:
        return "reduce risk watch"
    if is_overextended:
        return "avoid chasing"
    if total_score >= 65:
        return "accumulate on pullbacks"
    return "hold"


def score_from_indicators(
    df: pd.DataFrame,
    volatility_threshold_pct: float = 35.0,
    drawdown_threshold_pct: float = -15.0,
) -> ScoreBreakdown:
    """
    Score the latest row using trend, momentum, confirmation, and risk rules.

    Args:
        df: DataFrame containing at least OHLCV + indicator columns.
        volatility_threshold_pct: Volatility threshold for penalty.
        drawdown_threshold_pct: Drawdown threshold for penalty (negative number).
    """
    if df.empty:
        raise ScoringInputError("Cannot score empty DataFrame.")

    _validate_columns(df)

    latest = df.iloc[-1]
    close_now = float(latest["close"])
    close_20d_ago = float(df.iloc[-21]["close"]) if len(df) >= 21 else None

    trend_score = 0
    momentum_score = 0
    confirmation_score = 0
    risk_penalty = 0
    explanations: list[str] = []

    # 1) Trend score (max 40)
    if pd.notna(latest["sma_200"]) and close_now > float(latest["sma_200"]):
        trend_score += 20
        explanations.append("Price is above SMA200, so the long-term trend remains constructive (+20).")
    else:
        explanations.append("Price is below or near SMA200, so long-term trend confirmation is limited (+0).")

    if pd.notna(latest["sma_50"]) and pd.notna(latest["sma_200"]) and float(latest["sma_50"]) > float(latest["sma_200"]):
        trend_score += 10
        explanations.append("SMA50 is above SMA200, supporting medium-to-long-term trend alignment (+10).")
    else:
        explanations.append("SMA50 is not above SMA200, so medium-to-long-term alignment is not confirmed (+0).")

    if pd.notna(latest["sma_20"]) and pd.notna(latest["sma_50"]) and float(latest["sma_20"]) > float(latest["sma_50"]):
        trend_score += 10
        explanations.append("SMA20 is above SMA50, which supports a constructive near-term trend (+10).")
    else:
        explanations.append("SMA20 is not above SMA50, so near-term trend support is limited (+0).")

    # 2) Momentum score (max 25)
    rsi = float(latest["rsi_14"]) if pd.notna(latest["rsi_14"]) else None
    if rsi is not None and 50 <= rsi <= 65:
        momentum_score += 10
        explanations.append("RSI14 is between 50 and 65, a balanced momentum zone (+10).")
    else:
        explanations.append("RSI14 is outside the preferred 50-65 zone, so momentum is less balanced (+0).")

    if pd.notna(latest["macd_line"]) and pd.notna(latest["macd_signal"]) and float(latest["macd_line"]) > float(latest["macd_signal"]):
        momentum_score += 10
        explanations.append("MACD line is above MACD signal, indicating momentum is improving (+10).")
    else:
        explanations.append("MACD line is not above MACD signal, so momentum confirmation is limited (+0).")

    if close_20d_ago is not None and close_now > close_20d_ago:
        momentum_score += 5
        explanations.append("Latest close is above the close from 20 trading days ago, supporting near-term follow-through (+5).")
    else:
        explanations.append("Latest close is not above the close from 20 trading days ago, so follow-through is not confirmed (+0).")

    # 3) Confirmation score (max 15)
    if pd.notna(latest["avg_volume_20"]) and float(latest["volume"]) > float(latest["avg_volume_20"]):
        confirmation_score += 10
        explanations.append("Volume is above the 20-day average, adding participation confirmation (+10).")
    else:
        explanations.append("Volume is not above the 20-day average, so confirmation is lighter (+0).")

    distance_52w = (
        float(latest["distance_from_52w_high_pct"])
        if pd.notna(latest["distance_from_52w_high_pct"])
        else None
    )
    if distance_52w is not None and distance_52w >= -10:
        confirmation_score += 5
        explanations.append("Price is within 10% of the 52-week high, showing relative strength (+5).")
    else:
        explanations.append("Price is more than 10% below the 52-week high (+0).")

    # 4) Risk penalties (max -20)
    is_rsi_hot = rsi is not None and rsi > 75
    if is_rsi_hot:
        risk_penalty -= 5
        explanations.append("RSI14 is above 75, so avoid chasing at the current level (-5).")

    is_close_far_above_sma50 = (
        pd.notna(latest["sma_50"]) and close_now > float(latest["sma_50"]) * 1.10
    )
    if is_close_far_above_sma50:
        risk_penalty -= 5
        explanations.append("Price is more than 10% above SMA50, so it may be stretched short term (-5).")

    rolling_volatility = (
        float(latest["rolling_volatility_20_pct"])
        if pd.notna(latest["rolling_volatility_20_pct"])
        else None
    )
    if rolling_volatility is not None and rolling_volatility > volatility_threshold_pct:
        risk_penalty -= 5
        explanations.append(
            f"Rolling volatility ({rolling_volatility:.2f}%) is above threshold "
            f"({volatility_threshold_pct:.2f}%), so near-term swings may stay wide (-5)."
        )

    drawdown = (
        float(latest["drawdown_from_peak_pct"])
        if pd.notna(latest["drawdown_from_peak_pct"])
        else None
    )
    if drawdown is not None and drawdown < drawdown_threshold_pct:
        risk_penalty -= 5
        explanations.append(
            f"Drawdown ({drawdown:.2f}%) is worse than threshold "
            f"({drawdown_threshold_pct:.2f}%), so downside pressure remains elevated (-5)."
        )

    raw_total = trend_score + momentum_score + confirmation_score + risk_penalty
    total_score = max(0, min(100, raw_total))
    label = _get_label(total_score)
    is_overextended = is_rsi_hot or is_close_far_above_sma50
    action_summary = _get_action_summary(
        total_score=total_score,
        risk_penalty=risk_penalty,
        is_overextended=is_overextended,
    )

    return ScoreBreakdown(
        trend_score=trend_score,
        momentum_score=momentum_score,
        confirmation_score=confirmation_score,
        risk_penalty=risk_penalty,
        total_score=total_score,
        label=label,
        action_summary=action_summary,
        explanations=explanations,
    )
