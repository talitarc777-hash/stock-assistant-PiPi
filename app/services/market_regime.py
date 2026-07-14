"""Shared, transparent market-regime risk policy for research and live trading."""

from __future__ import annotations

import math
from typing import Any, Mapping


STRESS_BENCHMARK_RETURN_20D_PCT = -5.0
STRESS_TICKER_DRAWDOWN_PCT = -20.0
STRESS_VOLATILITY_20_PCT = 45.0
CAUTION_BENCHMARK_RETURN_20D_PCT = -2.0
CAUTION_TICKER_DRAWDOWN_PCT = -10.0
CAUTION_VOLATILITY_20_PCT = 30.0


def _finite_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def assess_market_regime(values: Mapping[str, Any]) -> dict[str, Any]:
    """Classify known-at-decision-time market stress and return a risk multiplier."""
    benchmark_return = _finite_float(values.get("benchmark_return_20d_pct"))
    drawdown = _finite_float(values.get("drawdown_from_peak_pct"))
    volatility = _finite_float(values.get("rolling_volatility_20_pct"))

    stress_reasons: list[str] = []
    caution_reasons: list[str] = []
    if benchmark_return is not None:
        if benchmark_return <= STRESS_BENCHMARK_RETURN_20D_PCT:
            stress_reasons.append("benchmark_20d_selloff")
        elif benchmark_return <= CAUTION_BENCHMARK_RETURN_20D_PCT:
            caution_reasons.append("benchmark_20d_weakness")
    if drawdown is not None:
        if drawdown <= STRESS_TICKER_DRAWDOWN_PCT:
            stress_reasons.append("ticker_deep_drawdown")
        elif drawdown <= CAUTION_TICKER_DRAWDOWN_PCT:
            caution_reasons.append("ticker_drawdown")
    if volatility is not None:
        if volatility >= STRESS_VOLATILITY_20_PCT:
            stress_reasons.append("extreme_volatility")
        elif volatility >= CAUTION_VOLATILITY_20_PCT:
            caution_reasons.append("elevated_volatility")

    if stress_reasons:
        level, multiplier, trade_allowed = "stress", 0.0, False
        reasons = stress_reasons + caution_reasons
    elif caution_reasons:
        level, multiplier, trade_allowed = "caution", 0.5, True
        reasons = caution_reasons
    else:
        level, multiplier, trade_allowed = "normal", 1.0, True
        reasons = []

    return {
        "level": level,
        "new_position_allowed": trade_allowed,
        "position_size_multiplier": multiplier,
        "reasons": reasons,
        "benchmark_return_20d_pct": benchmark_return,
        "ticker_drawdown_pct": drawdown,
        "volatility_20_pct": volatility,
    }
