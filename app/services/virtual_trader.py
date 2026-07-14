"""Virtual trader simulation driven by model walk-forward predictions.

This module is simulation-only:
- no broker connectivity
- no leverage
- no real-money execution

The goal is to answer a practical research question:
"If we contributed cash monthly and only acted on out-of-sample model predictions,
would the strategy have made or lost money over time?"
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.settings import get_settings
from app.services.market_data import get_price_history
from app.services.model_results import load_model_evaluation_table
from app.services.prediction_explanations import (
    build_benchmark_strength_summary,
    build_news_sentiment_summary,
    build_prediction_explanation,
    build_technical_state_summary,
    build_trade_action_summary,
    build_trade_explanation,
)
from app.services.research_pipeline import build_feature_dataset

logger = logging.getLogger(__name__)


class VirtualTraderError(Exception):
    """Raised when virtual trader inputs or state are invalid."""


@dataclass(frozen=True)
class VirtualTradeLogEntry:
    """One simulated trade-side event in the account timeline."""

    timestamp: str
    ticker: str
    action: str
    price: float
    quantity: float
    cash_after: float
    holdings_after: float
    entry_price: float | None
    exit_price: float | None
    position_size_value: float
    realized_pnl: float
    unrealized_pnl: float
    model_confidence: float | None
    trade_reason: str
    threshold_summary: str
    action_summary: str
    technical_state_summary: str
    news_sentiment_summary: str
    benchmark_strength_summary: str
    prediction_explanation: str
    explanation: str


@dataclass(frozen=True)
class MonthlyContributionRecord:
    """One monthly contribution record."""

    date: str
    amount: float
    cumulative_contributions: float


@dataclass(frozen=True)
class EquityCurvePoint:
    """One daily account value point for charting."""

    date: str
    cash: float
    holdings_value: float
    total_equity: float
    realized_pnl: float
    unrealized_pnl: float
    benchmark_equity: float


@dataclass(frozen=True)
class VirtualTraderArtifact:
    """Saved artifact paths for one virtual trader run."""

    ticker: str
    period: str
    model_name: str
    summary_path: Path
    trade_log_path: Path
    equity_curve_path: Path
    contribution_history_path: Path
    benchmark_comparison_path: Path


@dataclass(frozen=True)
class VirtualTraderResult:
    """Structured simulation result."""

    ticker: str
    period: str
    model_name: str
    summary: dict[str, Any]
    benchmark_comparison: dict[str, Any]
    trade_log: list[VirtualTradeLogEntry]
    equity_curve: list[EquityCurvePoint]
    contribution_history: list[MonthlyContributionRecord]
    artifact: VirtualTraderArtifact


def _validate_price_columns(df: pd.DataFrame) -> None:
    """Validate the minimum columns needed for account simulation."""
    required = ["date", "close"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise VirtualTraderError(f"Missing required price columns: {missing}")


def _validate_evaluation_columns(df: pd.DataFrame) -> None:
    """Validate required walk-forward evaluation fields."""
    required = ["prediction_date", "ticker", "predicted_value", "actual_future_result"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise VirtualTraderError(f"Missing required evaluation columns: {missing}")


def _prepare_price_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize price data into a sorted daily frame."""
    _validate_price_columns(df)
    result = df.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result = result.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    if result.empty:
        raise VirtualTraderError("Price history is empty after date cleaning.")
    return result


def _calculate_risk_metrics(
    equity_curve: list[EquityCurvePoint],
    contribution_history: list[MonthlyContributionRecord],
    equity_field: str,
) -> dict[str, float | int | None]:
    """Calculate cash-flow-adjusted daily risk and return diagnostics."""
    contributions_by_date: dict[str, float] = {}
    for item in contribution_history:
        contributions_by_date[item.date] = (
            contributions_by_date.get(item.date, 0.0) + float(item.amount)
        )

    returns: list[float] = []
    wealth = 1.0
    peak = 1.0
    max_drawdown = 0.0
    previous_equity: float | None = None
    for point in equity_curve:
        current_equity = float(getattr(point, equity_field))
        if previous_equity is not None and previous_equity > 0:
            external_flow = contributions_by_date.get(point.date, 0.0)
            daily_return = (
                current_equity - external_flow - previous_equity
            ) / previous_equity
            daily_return = max(-1.0, min(10.0, float(daily_return)))
            returns.append(daily_return)
            wealth *= 1.0 + daily_return
            peak = max(peak, wealth)
            if peak > 0:
                max_drawdown = min(max_drawdown, wealth / peak - 1.0)
        previous_equity = current_equity

    if not returns:
        return {
            "annualized_return_pct": None,
            "annualized_volatility_pct": None,
            "sharpe_ratio": None,
            "downside_deviation_pct": None,
            "max_drawdown_pct": None,
            "risk_observation_count": 0,
        }

    series = pd.Series(returns, dtype=float)
    mean_daily = float(series.mean())
    daily_volatility = float(series.std(ddof=1)) if len(series) > 1 else 0.0
    downside = series[series < 0]
    downside_daily = (
        float((downside.pow(2).mean()) ** 0.5)
        if not downside.empty
        else 0.0
    )
    annualized_return = (
        (wealth ** (252.0 / len(series))) - 1.0
        if wealth > 0
        else -1.0
    )
    annualized_volatility = daily_volatility * (252.0 ** 0.5)
    sharpe_ratio = (
        mean_daily / daily_volatility * (252.0 ** 0.5)
        if daily_volatility > 1e-12
        else None
    )
    return {
        "annualized_return_pct": float(annualized_return * 100.0),
        "annualized_volatility_pct": float(annualized_volatility * 100.0),
        "sharpe_ratio": float(sharpe_ratio) if sharpe_ratio is not None else None,
        "downside_deviation_pct": float(
            downside_daily * (252.0 ** 0.5) * 100.0
        ),
        "max_drawdown_pct": float(max_drawdown * 100.0),
        "risk_observation_count": int(len(series)),
    }


def _prepare_evaluation_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the walk-forward evaluation table used as trading signals."""
    _validate_evaluation_columns(df)
    result = df.copy()
    result["prediction_date"] = pd.to_datetime(result["prediction_date"], errors="coerce")
    result = result.dropna(subset=["prediction_date"]).sort_values("prediction_date").reset_index(drop=True)
    if result.empty:
        raise VirtualTraderError("Evaluation table is empty after date cleaning.")
    return result.drop_duplicates(subset=["prediction_date"], keep="last")


def _align_benchmark_to_dates(
    benchmark_df: pd.DataFrame,
    target_dates: pd.Series,
) -> pd.DataFrame:
    """Align benchmark close prices to the simulation calendar."""
    benchmark_prices = _prepare_price_frame(benchmark_df)[["date", "close"]].rename(
        columns={"close": "benchmark_close"}
    )
    aligned = pd.DataFrame({"date": pd.to_datetime(target_dates)}).merge(
        benchmark_prices,
        on="date",
        how="left",
    )
    aligned["benchmark_close"] = aligned["benchmark_close"].ffill().bfill()
    return aligned


def _is_first_trading_day_of_month(current_date: pd.Timestamp, previous_date: pd.Timestamp | None) -> bool:
    """Treat the first available market date in a month as the contribution date."""
    if previous_date is None:
        return True
    return (current_date.year, current_date.month) != (previous_date.year, previous_date.month)


def _get_month_key(current_date: pd.Timestamp) -> str:
    """Convert a trading date into its YYYY-MM month key."""
    return current_date.strftime("%Y-%m")


def _resolve_monthly_contribution_amount(
    current_date: pd.Timestamp,
    contribution_schedule: dict[str, float] | None,
    fallback_amount: float,
) -> float:
    """Choose the contribution amount for the current month.

    When a user-specific monthly schedule is provided, it takes priority.
    Otherwise we fall back to the original fixed monthly contribution behavior.
    """
    if not contribution_schedule:
        return float(fallback_amount)
    return float(contribution_schedule.get(_get_month_key(current_date), 0.0))


def _is_bullish_signal(row: pd.Series, task_type: str, min_predicted_return_pct: float) -> bool:
    """Interpret model output into a simple buy-or-not signal."""
    if task_type == "classification":
        return int(row["predicted_value"]) == 1
    return float(row["predicted_value"]) >= min_predicted_return_pct


def _is_bearish_signal(row: pd.Series, task_type: str) -> bool:
    """Interpret model output into a simple sell-or-not signal."""
    if task_type == "classification":
        return int(row["predicted_value"]) == 0
    return float(row["predicted_value"]) <= 0.0


def _passes_confidence_threshold(row: pd.Series, confidence_threshold: float) -> bool:
    """Allow entries/exits only when confidence is present and strong enough."""
    confidence = row.get("confidence_score")
    if confidence is None or pd.isna(confidence):
        return True
    return float(confidence) >= confidence_threshold


def _json_write(path: Path, payload: Any) -> None:
    """Write JSON with a consistent beginner-friendly format."""
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _build_trade_threshold_summary(
    action: str,
    confidence_threshold: float,
    confidence_score: float | None,
    max_position_size_pct: float,
    stop_loss_pct: float,
    take_profit_pct: float | None,
    min_predicted_return_pct: float,
    task_type: str,
    current_price: float,
    entry_price: float | None,
) -> str:
    """Summarize the rule thresholds behind one trade action."""
    parts: list[str] = []

    if confidence_score is None:
        parts.append(f"confidence threshold was {confidence_threshold:.0%} and confidence was unavailable")
    else:
        comparator = ">=" if confidence_score >= confidence_threshold else "<"
        parts.append(
            f"confidence {confidence_score:.0%} {comparator} required threshold {confidence_threshold:.0%}"
        )

    if action == "buy":
        parts.append(f"max position size was capped at {max_position_size_pct:.0%} of equity")
        if task_type == "regression":
            parts.append(f"predicted return threshold was {min_predicted_return_pct:.2f}%")
    else:
        if entry_price is not None:
            stop_level = entry_price * (1 - stop_loss_pct)
            parts.append(f"stop loss level was {stop_level:.2f} ({stop_loss_pct:.0%} below entry)")
            if take_profit_pct is not None:
                take_profit_level = entry_price * (1 + take_profit_pct)
                parts.append(f"take profit level was {take_profit_level:.2f}")
        parts.append(f"exit price was {current_price:.2f}")

    return "Thresholds: " + "; ".join(parts) + "."


def _save_virtual_trader_artifacts(
    ticker: str,
    period: str,
    model_name: str,
    summary: dict[str, Any],
    benchmark_comparison: dict[str, Any],
    trade_log: list[VirtualTradeLogEntry],
    equity_curve: list[EquityCurvePoint],
    contribution_history: list[MonthlyContributionRecord],
    output_dir: str | Path | None = None,
) -> VirtualTraderArtifact:
    """Save simulation outputs for later review and charting."""
    base_dir = Path(output_dir or get_settings().research_models_dir)
    artifact_dir = base_dir / ticker / period / "virtual_trader" / model_name
    artifact_dir.mkdir(parents=True, exist_ok=True)

    summary_path = artifact_dir / "summary.json"
    trade_log_path = artifact_dir / "trade_log.csv"
    equity_curve_path = artifact_dir / "equity_curve.csv"
    contribution_history_path = artifact_dir / "monthly_contributions.csv"
    benchmark_comparison_path = artifact_dir / "benchmark_comparison.json"

    _json_write(summary_path, summary)
    _json_write(benchmark_comparison_path, benchmark_comparison)
    pd.DataFrame([item.__dict__ for item in trade_log]).to_csv(trade_log_path, index=False)
    pd.DataFrame([item.__dict__ for item in equity_curve]).to_csv(equity_curve_path, index=False)
    pd.DataFrame([item.__dict__ for item in contribution_history]).to_csv(
        contribution_history_path,
        index=False,
    )

    return VirtualTraderArtifact(
        ticker=ticker,
        period=period,
        model_name=model_name,
        summary_path=summary_path,
        trade_log_path=trade_log_path,
        equity_curve_path=equity_curve_path,
        contribution_history_path=contribution_history_path,
        benchmark_comparison_path=benchmark_comparison_path,
    )


def simulate_virtual_trader(
    ticker: str,
    period: str,
    model_name: str,
    price_df: pd.DataFrame,
    evaluation_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    benchmark_symbol: str = "VOO",
    monthly_contribution_usd: float = 1_000.0,
    contribution_schedule: dict[str, float] | None = None,
    initial_cash: float = 0.0,
    confidence_threshold: float = 0.55,
    max_position_size_pct: float = 0.25,
    stop_loss_pct: float = 0.10,
    take_profit_pct: float | None = None,
    task_type: str = "classification",
    min_predicted_return_pct: float = 0.0,
    output_dir: str | Path | None = None,
) -> VirtualTraderResult:
    """Run a transparent no-leverage virtual trader simulation.

    Important:
    - We only use walk-forward, out-of-sample predictions as trading signals.
    - No leverage is allowed: position size is always capped by available cash.
    - Monthly contributions are added on the first trading day of each month.
    """
    if monthly_contribution_usd < 0:
        raise VirtualTraderError("monthly_contribution_usd must be >= 0.")
    if initial_cash < 0:
        raise VirtualTraderError("initial_cash must be >= 0.")
    if not 0 < max_position_size_pct <= 1:
        raise VirtualTraderError("max_position_size_pct must be within (0, 1].")
    if stop_loss_pct < 0:
        raise VirtualTraderError("stop_loss_pct must be >= 0.")
    if take_profit_pct is not None and take_profit_pct <= 0:
        raise VirtualTraderError("take_profit_pct must be > 0 when provided.")
    if not 0 <= confidence_threshold <= 1:
        raise VirtualTraderError("confidence_threshold must be between 0 and 1.")

    ticker_symbol = ticker.strip().upper()
    benchmark_symbol = benchmark_symbol.strip().upper()
    price_work_df = _prepare_price_frame(price_df)
    eval_work_df = _prepare_evaluation_frame(evaluation_df)
    aligned_benchmark_df = _align_benchmark_to_dates(benchmark_df, price_work_df["date"])
    work_df = price_work_df.merge(aligned_benchmark_df, on="date", how="left")

    signal_by_date = {
        row["prediction_date"].strftime("%Y-%m-%d"): row
        for _, row in eval_work_df.iterrows()
    }

    cash = float(initial_cash)
    shares = 0.0
    avg_entry_price: float | None = None
    realized_pnl = 0.0
    total_contributions = float(initial_cash)

    benchmark_cash = float(initial_cash)
    benchmark_shares = 0.0

    trade_log: list[VirtualTradeLogEntry] = []
    contribution_history: list[MonthlyContributionRecord] = []
    equity_curve: list[EquityCurvePoint] = []

    previous_date: pd.Timestamp | None = None

    logger.info(
        "Running virtual trader ticker=%s model=%s period=%s rows=%d",
        ticker_symbol,
        model_name,
        period,
        len(work_df),
    )

    for _, row in work_df.iterrows():
        current_date = pd.to_datetime(row["date"])
        date_str = current_date.strftime("%Y-%m-%d")
        close_price = float(row["close"])
        benchmark_close = float(row["benchmark_close"])

        if _is_first_trading_day_of_month(current_date, previous_date):
            contribution_amount = _resolve_monthly_contribution_amount(
                current_date=current_date,
                contribution_schedule=contribution_schedule,
                fallback_amount=monthly_contribution_usd,
            )
            cash += contribution_amount
            benchmark_cash += contribution_amount
            total_contributions += contribution_amount
            contribution_history.append(
                MonthlyContributionRecord(
                    date=date_str,
                    amount=float(contribution_amount),
                    cumulative_contributions=float(total_contributions),
                )
            )

        if benchmark_close > 0 and benchmark_cash > 0:
            benchmark_shares += benchmark_cash / benchmark_close
            benchmark_cash = 0.0

        holdings_value_before = shares * close_price
        total_equity_before = cash + holdings_value_before

        signal_row = signal_by_date.get(date_str)
        should_exit = False
        exit_reason = ""
        signal_confidence = None if signal_row is None or pd.isna(signal_row.get("confidence_score")) else float(
            signal_row.get("confidence_score")
        )
        technical_summary = (
            str(signal_row.get("technical_state_summary"))
            if signal_row is not None and pd.notna(signal_row.get("technical_state_summary"))
            else build_technical_state_summary(row)
        )
        news_summary = (
            str(signal_row.get("news_sentiment_summary"))
            if signal_row is not None and pd.notna(signal_row.get("news_sentiment_summary"))
            else build_news_sentiment_summary(row)
        )
        benchmark_summary = (
            str(signal_row.get("benchmark_strength_summary"))
            if signal_row is not None and pd.notna(signal_row.get("benchmark_strength_summary"))
            else build_benchmark_strength_summary(row)
        )
        signal_explanation = (
            str(signal_row.get("explanation"))
            if signal_row is not None and pd.notna(signal_row.get("explanation"))
            else None
        )

        if signal_explanation is None and signal_row is not None:
            generated_explanation = build_prediction_explanation(
                feature_row=row,
                task_type=task_type,
                predicted_value=signal_row.get("predicted_value", 0),
                confidence_score=signal_confidence,
            )
            signal_explanation = generated_explanation["explanation"]
            technical_summary = generated_explanation["technical_state_summary"]
            news_summary = generated_explanation["news_sentiment_summary"]
            benchmark_summary = generated_explanation["benchmark_strength_summary"]

        if shares > 0 and avg_entry_price is not None:
            if stop_loss_pct > 0 and close_price <= avg_entry_price * (1 - stop_loss_pct):
                should_exit = True
                exit_reason = "stop_loss"
            elif take_profit_pct is not None and close_price >= avg_entry_price * (1 + take_profit_pct):
                should_exit = True
                exit_reason = "take_profit"
            elif signal_row is not None and _passes_confidence_threshold(signal_row, confidence_threshold):
                if _is_bearish_signal(signal_row, task_type=task_type):
                    should_exit = True
                    exit_reason = "model_bearish_signal"

        if should_exit and shares > 0:
            sale_value = shares * close_price
            trade_realized_pnl = (close_price - float(avg_entry_price)) * shares if avg_entry_price else 0.0
            cash += sale_value
            realized_pnl += trade_realized_pnl
            threshold_summary = _build_trade_threshold_summary(
                action="sell",
                confidence_threshold=confidence_threshold,
                confidence_score=float(signal_confidence) if signal_confidence is not None else None,
                max_position_size_pct=max_position_size_pct,
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
                min_predicted_return_pct=min_predicted_return_pct,
                task_type=task_type,
                current_price=close_price,
                entry_price=float(avg_entry_price) if avg_entry_price is not None else None,
            )
            action_summary = build_trade_action_summary(action="sell", trade_reason=exit_reason)

            trade_log.append(
                VirtualTradeLogEntry(
                    timestamp=date_str,
                    ticker=ticker_symbol,
                    action="sell",
                    price=float(close_price),
                    quantity=float(shares),
                    cash_after=float(cash),
                    holdings_after=0.0,
                    entry_price=float(avg_entry_price) if avg_entry_price is not None else None,
                    exit_price=float(close_price),
                    position_size_value=0.0,
                    realized_pnl=float(realized_pnl),
                    unrealized_pnl=0.0,
                    model_confidence=signal_confidence,
                    trade_reason=exit_reason,
                    threshold_summary=threshold_summary,
                    action_summary=action_summary,
                    technical_state_summary=technical_summary,
                    news_sentiment_summary=news_summary,
                    benchmark_strength_summary=benchmark_summary,
                    prediction_explanation=signal_explanation or "",
                    explanation=build_trade_explanation(
                        action="sell",
                        trade_reason=exit_reason,
                        threshold_summary=threshold_summary,
                        signal_explanation=signal_explanation,
                    ),
                )
            )
            shares = 0.0
            avg_entry_price = None

        holdings_value_after_exit = shares * close_price
        total_equity_after_exit = cash + holdings_value_after_exit

        can_enter = (
            shares == 0
            and signal_row is not None
            and _passes_confidence_threshold(signal_row, confidence_threshold)
            and _is_bullish_signal(signal_row, task_type=task_type, min_predicted_return_pct=min_predicted_return_pct)
        )

        if can_enter and close_price > 0:
            max_position_value = total_equity_after_exit * max_position_size_pct
            buy_value = min(cash, max_position_value)

            if buy_value > 0:
                buy_shares = buy_value / close_price
                shares = float(buy_shares)
                cash -= buy_value
                avg_entry_price = float(close_price)
                unrealized_pnl = 0.0
                threshold_summary = _build_trade_threshold_summary(
                    action="buy",
                    confidence_threshold=confidence_threshold,
                    confidence_score=signal_confidence,
                    max_position_size_pct=max_position_size_pct,
                    stop_loss_pct=stop_loss_pct,
                    take_profit_pct=take_profit_pct,
                    min_predicted_return_pct=min_predicted_return_pct,
                    task_type=task_type,
                    current_price=close_price,
                    entry_price=float(avg_entry_price),
                )
                action_summary = build_trade_action_summary(action="buy", trade_reason="model_bullish_signal")

                trade_log.append(
                    VirtualTradeLogEntry(
                        timestamp=date_str,
                        ticker=ticker_symbol,
                        action="buy",
                        price=float(close_price),
                        quantity=float(shares),
                        cash_after=float(cash),
                        holdings_after=float(shares),
                        entry_price=float(avg_entry_price),
                        exit_price=None,
                        position_size_value=float(shares * close_price),
                        realized_pnl=float(realized_pnl),
                        unrealized_pnl=float(unrealized_pnl),
                        model_confidence=signal_confidence,
                        trade_reason="model_bullish_signal",
                        threshold_summary=threshold_summary,
                        action_summary=action_summary,
                        technical_state_summary=technical_summary,
                        news_sentiment_summary=news_summary,
                        benchmark_strength_summary=benchmark_summary,
                        prediction_explanation=signal_explanation or "",
                        explanation=build_trade_explanation(
                            action="buy",
                            trade_reason="model_bullish_signal",
                            threshold_summary=threshold_summary,
                            signal_explanation=signal_explanation,
                        ),
                    )
                )

        holdings_value = shares * close_price
        unrealized_pnl = 0.0 if shares == 0 or avg_entry_price is None else (close_price - avg_entry_price) * shares
        total_equity = cash + holdings_value
        benchmark_equity = benchmark_cash + benchmark_shares * benchmark_close

        equity_curve.append(
            EquityCurvePoint(
                date=date_str,
                cash=float(cash),
                holdings_value=float(holdings_value),
                total_equity=float(total_equity),
                realized_pnl=float(realized_pnl),
                unrealized_pnl=float(unrealized_pnl),
                benchmark_equity=float(benchmark_equity),
            )
        )

        previous_date = current_date

    if not equity_curve:
        raise VirtualTraderError("Simulation produced no equity curve points.")

    final_equity = equity_curve[-1].total_equity
    benchmark_final_equity = equity_curve[-1].benchmark_equity
    unrealized_pnl = equity_curve[-1].unrealized_pnl
    strategy_risk = _calculate_risk_metrics(
        equity_curve,
        contribution_history,
        "total_equity",
    )
    benchmark_risk = _calculate_risk_metrics(
        equity_curve,
        contribution_history,
        "benchmark_equity",
    )

    benchmark_comparison = {
        "benchmark": benchmark_symbol,
        "final_equity": float(benchmark_final_equity),
        "total_contributions": float(total_contributions),
        "return_on_contributions_pct": (
            float((benchmark_final_equity / total_contributions - 1) * 100) if total_contributions > 0 else 0.0
        ),
        **benchmark_risk,
    }

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker_symbol,
        "period": period,
        "model_name": model_name,
        "mode": "simulation_only_no_real_money_no_leverage",
        "task_type": task_type,
        "monthly_contribution_usd": (
            float(monthly_contribution_usd) if not contribution_schedule else None
        ),
        "contribution_mode": "custom_monthly_schedule" if contribution_schedule else "fixed_monthly_amount",
        "initial_cash": float(initial_cash),
        "confidence_threshold": float(confidence_threshold),
        "max_position_size_pct": float(max_position_size_pct),
        "stop_loss_pct": float(stop_loss_pct),
        "take_profit_pct": float(take_profit_pct) if take_profit_pct is not None else None,
        "total_contributions": float(total_contributions),
        "cash": float(cash),
        "holdings": float(shares),
        "entry_price": float(avg_entry_price) if avg_entry_price is not None else None,
        "exit_price": None,
        "realized_pnl": float(realized_pnl),
        "unrealized_pnl": float(unrealized_pnl),
        "final_equity": float(final_equity),
        "return_on_contributions_pct": (
            float((final_equity / total_contributions - 1) * 100) if total_contributions > 0 else 0.0
        ),
        "trade_count": int(len(trade_log)),
        "benchmark_symbol": benchmark_symbol,
        "benchmark_final_equity": float(benchmark_final_equity),
        "outperformance_vs_benchmark_pct_points": (
            float((final_equity / total_contributions - benchmark_final_equity / total_contributions) * 100)
            if total_contributions > 0
            else 0.0
        ),
        "annualized_return_pct": strategy_risk["annualized_return_pct"],
        "annualized_volatility_pct": strategy_risk["annualized_volatility_pct"],
        "sharpe_ratio": strategy_risk["sharpe_ratio"],
        "downside_deviation_pct": strategy_risk["downside_deviation_pct"],
        "max_drawdown_pct": strategy_risk["max_drawdown_pct"],
        "risk_observation_count": strategy_risk["risk_observation_count"],
        "benchmark_annualized_return_pct": benchmark_risk["annualized_return_pct"],
        "benchmark_annualized_volatility_pct": benchmark_risk["annualized_volatility_pct"],
        "benchmark_sharpe_ratio": benchmark_risk["sharpe_ratio"],
        "benchmark_max_drawdown_pct": benchmark_risk["max_drawdown_pct"],
    }

    artifact = _save_virtual_trader_artifacts(
        ticker=ticker_symbol,
        period=period,
        model_name=model_name,
        summary=summary,
        benchmark_comparison=benchmark_comparison,
        trade_log=trade_log,
        equity_curve=equity_curve,
        contribution_history=contribution_history,
        output_dir=output_dir,
    )

    return VirtualTraderResult(
        ticker=ticker_symbol,
        period=period,
        model_name=model_name,
        summary=summary,
        benchmark_comparison=benchmark_comparison,
        trade_log=trade_log,
        equity_curve=equity_curve,
        contribution_history=contribution_history,
        artifact=artifact,
    )


def run_virtual_trader_from_model(
    ticker: str,
    period: str = "5y",
    benchmark: str = "VOO",
    target_name: str = "target_5d_updown",
    task_type: str = "classification",
    model_name: str = "logistic_regression",
    monthly_contribution_usd: float = 1_000.0,
    contribution_schedule: dict[str, float] | None = None,
    initial_cash: float = 0.0,
    confidence_threshold: float = 0.55,
    max_position_size_pct: float = 0.25,
    stop_loss_pct: float = 0.10,
    take_profit_pct: float | None = None,
    min_predicted_return_pct: float = 0.0,
    include_news_sentiment: bool = True,
    sentiment_model: str = "finbert",
    output_dir: str | Path | None = None,
) -> VirtualTraderResult:
    """Train one baseline model, then simulate trading from walk-forward predictions."""
    from app.services.model_training import train_baseline_model  # local import keeps this module usable without sklearn

    ticker_symbol = ticker.strip().upper()
    benchmark_symbol = benchmark.strip().upper()

    dataset_df = build_feature_dataset(
        ticker=ticker_symbol,
        period=period,
        benchmark=benchmark_symbol,
        include_news_sentiment=include_news_sentiment,
        sentiment_model=sentiment_model,
    )
    training_result = train_baseline_model(
        dataset_df=dataset_df,
        ticker=ticker_symbol,
        period=period,
        target_name=target_name,
        task_type=task_type,
        model_name=model_name,
        output_dir=output_dir,
    )
    price_df = dataset_df.copy()
    benchmark_df = get_price_history(benchmark_symbol, period=period)

    return simulate_virtual_trader(
        ticker=ticker_symbol,
        period=period,
        model_name=model_name,
        price_df=price_df,
        evaluation_df=training_result.evaluation_table,
        benchmark_df=benchmark_df,
        benchmark_symbol=benchmark_symbol,
        monthly_contribution_usd=monthly_contribution_usd,
        contribution_schedule=contribution_schedule,
        initial_cash=initial_cash,
        confidence_threshold=confidence_threshold,
        max_position_size_pct=max_position_size_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        task_type=task_type,
        min_predicted_return_pct=min_predicted_return_pct,
        output_dir=output_dir,
    )


def run_virtual_trader_from_saved_evaluation(
    ticker: str,
    period: str = "5y",
    benchmark: str = "VOO",
    target_name: str = "target_5d_updown",
    task_type: str = "classification",
    model_name: str = "logistic_regression",
    contribution_schedule: dict[str, float] | None = None,
    monthly_contribution_usd: float = 1_000.0,
    initial_cash: float = 0.0,
    confidence_threshold: float = 0.55,
    max_position_size_pct: float = 0.25,
    stop_loss_pct: float = 0.10,
    take_profit_pct: float | None = None,
    min_predicted_return_pct: float = 0.0,
    output_dir: str | Path | None = None,
) -> VirtualTraderResult:
    """Run a simulation from an already-saved evaluation table.

    This keeps the web UI responsive to user-specific monthly contribution records
    and model selection without retraining the model on every request.
    """
    ticker_symbol = ticker.strip().upper()
    benchmark_symbol = benchmark.strip().upper()
    price_df = build_feature_dataset(
        ticker=ticker_symbol,
        period=period,
        benchmark=benchmark_symbol,
        include_news_sentiment=True,
        sentiment_model="finbert",
    )
    benchmark_df = get_price_history(benchmark_symbol, period=period)
    evaluation_df = load_model_evaluation_table(
        ticker=ticker_symbol,
        period=period,
        target_name=target_name,
        model_name=model_name,
    )

    return simulate_virtual_trader(
        ticker=ticker_symbol,
        period=period,
        model_name=model_name,
        price_df=price_df,
        evaluation_df=evaluation_df,
        benchmark_df=benchmark_df,
        benchmark_symbol=benchmark_symbol,
        monthly_contribution_usd=monthly_contribution_usd,
        contribution_schedule=contribution_schedule,
        initial_cash=initial_cash,
        confidence_threshold=confidence_threshold,
        max_position_size_pct=max_position_size_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        task_type=task_type,
        min_predicted_return_pct=min_predicted_return_pct,
        output_dir=output_dir,
    )
