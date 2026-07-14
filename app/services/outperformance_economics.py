"""Cost-adjusted economics checks for benchmark-outperformance classifiers."""

from __future__ import annotations

from typing import Any

import pandas as pd


MIN_ACTIVE_SIGNALS = 10
MIN_PROFITABLE_SIGNAL_RATE = 0.48
MIN_PROFITABLE_PATH_RATE = 0.60
MAX_ALLOWED_DRAWDOWN_PCT = -25.0
NON_OVERLAPPING_HORIZON_ROWS = 5


def evaluate_outperformance_economics(
    evaluation_table: pd.DataFrame,
    dataset_df: pd.DataFrame,
    *,
    round_trip_cost_pct: float,
) -> dict[str, Any]:
    """Measure whether predicted relative winners made absolute money after costs."""
    required_evaluation = {"prediction_date", "predicted_value"}
    required_dataset = {
        "date",
        "target_5d_return",
        "target_5d_excess_return",
    }
    if not required_evaluation.issubset(evaluation_table.columns):
        return {"passed": False, "reasons": ["economics_evaluation_missing"]}
    if not required_dataset.issubset(dataset_df.columns):
        return {"passed": False, "reasons": ["economics_targets_missing"]}

    evaluation = evaluation_table.copy()
    dataset = dataset_df.copy()
    evaluation["prediction_date"] = pd.to_datetime(
        evaluation["prediction_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    dataset["prediction_date"] = pd.to_datetime(
        dataset["date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    merge_keys = ["prediction_date"]
    if "source_ticker" in evaluation.columns and "ticker" in dataset.columns:
        evaluation["source_ticker"] = evaluation["source_ticker"].astype(str).str.upper()
        dataset["source_ticker"] = dataset["ticker"].astype(str).str.upper()
        merge_keys.append("source_ticker")
    joined = evaluation.merge(
        dataset[
            merge_keys + ["target_5d_return", "target_5d_excess_return"]
        ],
        on=merge_keys,
        how="inner",
        validate="many_to_one",
    )
    for column in ("predicted_value", "target_5d_return", "target_5d_excess_return"):
        joined[column] = pd.to_numeric(joined[column], errors="coerce")
    joined = joined.dropna(
        subset=["predicted_value", "target_5d_return", "target_5d_excess_return"]
    ).reset_index(drop=True)
    regime_filter_applied = "is_regime_trade_allowed" in joined.columns
    regime_allowed = pd.Series(True, index=joined.index)
    if regime_filter_applied:
        regime_allowed = joined["is_regime_trade_allowed"].astype(str).str.lower().isin(
            {"true", "1", "yes"}
        )
    position_multiplier_applied = "market_regime_position_multiplier" in joined.columns
    position_multiplier = pd.Series(1.0, index=joined.index)
    if position_multiplier_applied:
        position_multiplier = pd.to_numeric(
            joined["market_regime_position_multiplier"], errors="coerce"
        ).fillna(1.0).clip(lower=0.0, upper=1.0)
    active = (joined["predicted_value"] > 0) & regime_allowed & (position_multiplier > 0)
    joined["net_strategy_return_pct"] = (
        joined["target_5d_return"] - float(round_trip_cost_pct)
    ) * position_multiplier
    active_rows = joined[active].copy()
    active_rows["net_return_pct"] = active_rows["net_strategy_return_pct"]

    path_metrics: list[dict[str, Any]] = []
    for offset in range(NON_OVERLAPPING_HORIZON_ROWS):
        path = joined.iloc[offset::NON_OVERLAPPING_HORIZON_ROWS]
        path_active = active.loc[path.index]
        net_returns = path.loc[path_active, "net_strategy_return_pct"]
        wealth = 1.0
        peak = 1.0
        max_drawdown = 0.0
        for value in net_returns:
            wealth *= 1.0 + max(-100.0, float(value)) / 100.0
            peak = max(peak, wealth)
            max_drawdown = min(max_drawdown, wealth / peak - 1.0)
        path_metrics.append(
            {
                "offset": offset,
                "signal_count": int(len(net_returns)),
                "average_net_return_pct": float(net_returns.mean()) if len(net_returns) else 0.0,
                "profitable_signal_rate": float((net_returns > 0).mean()) if len(net_returns) else 0.0,
                "cumulative_return_pct": (wealth - 1.0) * 100.0,
                "max_drawdown_pct": max_drawdown * 100.0,
            }
        )

    average_net_return = (
        float(active_rows["net_return_pct"].mean()) if len(active_rows) else 0.0
    )
    profitable_signal_rate = (
        float((active_rows["net_return_pct"] > 0).mean()) if len(active_rows) else 0.0
    )
    profitable_path_rate = float(
        sum(item["average_net_return_pct"] > 0 for item in path_metrics)
        / len(path_metrics)
    )
    worst_drawdown = min(item["max_drawdown_pct"] for item in path_metrics)
    reasons: list[str] = []
    if len(active_rows) < MIN_ACTIVE_SIGNALS:
        reasons.append("insufficient_active_signals")
    if average_net_return <= 0:
        reasons.append("negative_average_net_stock_return")
    if profitable_signal_rate < MIN_PROFITABLE_SIGNAL_RATE:
        reasons.append("profitable_stock_signal_rate_below_minimum")
    if profitable_path_rate < MIN_PROFITABLE_PATH_RATE:
        reasons.append("stock_returns_not_robust_across_non_overlapping_paths")
    if worst_drawdown < MAX_ALLOWED_DRAWDOWN_PCT:
        reasons.append("historical_stock_signal_drawdown_too_large")

    return {
        "passed": not reasons,
        "reasons": reasons,
        "evaluation_rows": int(len(joined)),
        "active_signal_count": int(len(active_rows)),
        "round_trip_cost_pct": float(round_trip_cost_pct),
        "average_net_stock_return_pct": average_net_return,
        "median_net_stock_return_pct": (
            float(active_rows["net_return_pct"].median()) if len(active_rows) else 0.0
        ),
        "profitable_stock_signal_rate": profitable_signal_rate,
        "average_excess_return_pct": (
            float(active_rows["target_5d_excess_return"].mean()) if len(active_rows) else 0.0
        ),
        "profitable_non_overlapping_path_rate": profitable_path_rate,
        "worst_path_drawdown_pct": worst_drawdown,
        "regime_filter_applied": regime_filter_applied,
        "position_multiplier_applied": position_multiplier_applied,
        "paths": path_metrics,
    }
