"""Isolated model-design benchmark; never publishes or registers runtime models.

This script deliberately keeps every output below ``data/model_design_experiments``.
The production model directory, lifecycle registry, and active-version pointers are
not read for adoption and are never written by this experiment.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import sys
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.metrics import (
    balanced_accuracy_score,
    brier_score_loss,
    matthews_corrcoef,
    mean_absolute_error,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.market_config import model_security_root
from app.services.market_regime import assess_market_regime
from app.services.model_lifecycle_service import (
    PROMOTION_EXECUTION_COST_PCT,
    PRODUCTION_MIN_SCORE,
    ModelLifecycleService,
)
from app.services.model_training import (
    VALIDATION_SCHEME_VERSION,
    _build_classifier_pipeline,
    _build_feature_frame,
    _build_regressor_pipeline,
    _calibrate_regression_abstention_threshold,
    _choose_time_series_splits,
    _purged_date_splits,
    prepare_stationary_feature_dataset,
    train_baseline_model,
)
from app.services.research_pipeline import build_feature_dataset
from scripts.audit_model_quality import audit_saved_models

logger = logging.getLogger(__name__)

TARGET = "target_5d_return"
HORIZON_ROWS = 5
ROUND_TRIP_COST_PCT = PROMOTION_EXECUTION_COST_PCT * 2.0
CURRENT_ALGORITHMS = (
    "linear_regression",
    "ridge_regression",
    "random_forest",
    "gradient_boosting",
)
MODEL_PERIODS = ("2y", "5y", "10y")


@dataclass(frozen=True)
class SecuritySample:
    market: str
    ticker: str
    asset_class: str
    experiment_core: bool = False


# Fixed before any result is inspected. These symbols are all members of the
# repository's production/configured universe or the active HK default cohort.
REPRESENTATIVE_UNIVERSE: tuple[SecuritySample, ...] = (
    SecuritySample("US", "VOO", "etf", True),
    SecuritySample("US", "QQQ", "etf", True),
    SecuritySample("US", "VTV", "etf", True),
    SecuritySample("US", "IWM", "etf"),
    SecuritySample("US", "XLE", "etf"),
    SecuritySample("US", "XLP", "etf"),
    SecuritySample("US", "MSFT", "stock", True),
    SecuritySample("US", "NVDA", "stock", True),
    SecuritySample("US", "JPM", "stock", True),
    SecuritySample("US", "XOM", "stock", True),
    SecuritySample("US", "JNJ", "stock"),
    SecuritySample("US", "F", "stock"),
    SecuritySample("US", "RIVN", "stock"),
    SecuritySample("US", "PLD", "reit", True),
    SecuritySample("US", "DLR", "reit", True),
    SecuritySample("US", "EXR", "reit", True),
    SecuritySample("HK", "0005", "stock", True),
    SecuritySample("HK", "0700", "stock", True),
    SecuritySample("HK", "1810", "stock"),
    SecuritySample("HK", "3690", "stock"),
    SecuritySample("HK", "9988", "stock", True),
)

EXPERIMENT_COHORT = tuple(item for item in REPRESENTATIVE_UNIVERSE if item.experiment_core)

COMPACT_FEATURES: tuple[str, ...] = (
    "return_5d_pct",
    "return_20d_pct",
    "return_6m_pct",
    "excess_return_3m_pct",
    "rolling_volatility_20_pct",
    "drawdown_from_peak_pct",
    "rsi_14",
    "close_vs_sma_200_pct",
    "macd_histogram_pct",
    "volume_vs_20d_avg",
)

FEATURE_PURPOSES: dict[str, str] = {
    "return_1d_pct": "very-short momentum",
    "return_5d_pct": "short momentum",
    "return_20d_pct": "medium momentum",
    "return_1m_pct": "near-duplicate monthly momentum",
    "return_3m_pct": "quarterly momentum",
    "return_6m_pct": "long momentum",
    "return_12m_pct": "annual momentum",
    "rsi_14": "momentum/overbought state",
    "distance_from_52w_high_pct": "distance from annual high",
    "rolling_volatility_20_pct": "short-horizon volatility",
    "drawdown_from_peak_pct": "drawdown risk",
    "benchmark_strength_score": "coarse benchmark-relative breadth",
    "overnight_gap_pct": "overnight price pressure",
    "intraday_range_pct": "intraday volatility",
    "close_location_in_range": "intraday close strength",
    "close_vs_sma_20_pct": "short trend distance",
    "close_vs_sma_50_pct": "medium trend distance",
    "close_vs_sma_200_pct": "long trend distance",
    "close_vs_ema_12_pct": "fast exponential trend distance",
    "close_vs_ema_26_pct": "slow exponential trend distance",
    "macd_line_pct": "trend momentum level",
    "macd_signal_pct": "smoothed trend momentum",
    "macd_histogram_pct": "trend-momentum change",
    "volume_vs_20d_avg": "relative volume",
}


def feature_purpose(feature: str) -> str:
    """Return one stable, human-readable purpose for a model feature."""
    if feature in FEATURE_PURPOSES:
        return FEATURE_PURPOSES[feature]
    if feature.startswith("benchmark_return_"):
        return "benchmark momentum"
    if feature.startswith("excess_return_"):
        return "benchmark-relative momentum"
    return "technical market state"


def _feature_priority(feature: str, original_position: int) -> tuple[int, int]:
    preferred = {
        "return_1d_pct": 10,
        "return_5d_pct": 11,
        "return_20d_pct": 12,
        "return_3m_pct": 13,
        "return_6m_pct": 14,
        "return_12m_pct": 15,
        "rsi_14": 20,
        "rolling_volatility_20_pct": 21,
        "drawdown_from_peak_pct": 22,
        "excess_return_5d_pct": 30,
        "excess_return_20d_pct": 31,
        "excess_return_3m_pct": 32,
        "excess_return_6m_pct": 33,
        "benchmark_return_5d_pct": 40,
        "benchmark_return_20d_pct": 41,
        "benchmark_return_3m_pct": 42,
        "close_vs_sma_20_pct": 50,
        "close_vs_sma_50_pct": 51,
        "close_vs_sma_200_pct": 52,
        "macd_histogram_pct": 60,
        "volume_vs_20d_avg": 70,
    }
    return preferred.get(feature, 100 + original_position), original_position


def select_training_features(
    training_frame: pd.DataFrame,
    candidate_features: list[str],
    design: str,
    *,
    correlation_threshold: float = 0.95,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Select features using only the supplied training fold.

    ``reduced`` removes empty/constant columns first and then greedily removes
    one member of highly correlated pairs. ``compact`` uses a predeclared core
    set but still drops a core field if it is constant in the training fold.
    """
    clean_design = str(design).strip().lower()
    if clean_design not in {"current", "reduced", "compact"}:
        raise ValueError(f"Unsupported feature design: {design}")
    if clean_design == "current":
        return list(candidate_features), [
            {
                "feature": feature,
                "purpose": feature_purpose(feature),
                "kept": True,
                "reason": "current_feature_set",
                "correlated_with": None,
                "absolute_correlation": None,
            }
            for feature in candidate_features
        ]

    base = (
        [feature for feature in COMPACT_FEATURES if feature in candidate_features]
        if clean_design == "compact"
        else list(candidate_features)
    )
    decisions: dict[str, dict[str, Any]] = {}
    nonconstant: list[str] = []
    for feature in base:
        numeric = pd.to_numeric(training_frame[feature], errors="coerce").dropna()
        if numeric.empty or int(numeric.nunique()) <= 1 or float(numeric.var(ddof=0)) <= 1e-12:
            decisions[feature] = {
                "feature": feature,
                "purpose": feature_purpose(feature),
                "kept": False,
                "reason": "constant_or_empty_in_training_fold",
                "correlated_with": None,
                "absolute_correlation": None,
            }
        else:
            nonconstant.append(feature)

    ordered = sorted(
        nonconstant,
        key=lambda feature: _feature_priority(feature, candidate_features.index(feature)),
    )
    selected: list[str] = []
    numeric_frame = training_frame[ordered].apply(pd.to_numeric, errors="coerce")
    correlation = numeric_frame.corr().abs()
    for feature in ordered:
        redundant_with: str | None = None
        redundant_value: float | None = None
        if clean_design == "reduced":
            for kept in selected:
                value = correlation.at[feature, kept]
                if pd.notna(value) and float(value) >= correlation_threshold:
                    redundant_with = kept
                    redundant_value = float(value)
                    break
        if redundant_with:
            decisions[feature] = {
                "feature": feature,
                "purpose": feature_purpose(feature),
                "kept": False,
                "reason": "training_fold_redundancy",
                "correlated_with": redundant_with,
                "absolute_correlation": redundant_value,
            }
        else:
            selected.append(feature)
            decisions[feature] = {
                "feature": feature,
                "purpose": feature_purpose(feature),
                "kept": True,
                "reason": "predeclared_compact_core" if clean_design == "compact" else "retained_after_training_only_reduction",
                "correlated_with": None,
                "absolute_correlation": None,
            }

    if not selected:
        raise ValueError(f"Feature selection removed every {clean_design} feature.")
    return selected, [decisions[feature] for feature in base]


def derive_economic_class_threshold(
    training_returns: Iterable[float],
    *,
    round_trip_cost_pct: float = ROUND_TRIP_COST_PCT,
) -> dict[str, float | int | str]:
    """Derive a BUY/neutral/SELL boundary from training-only robust uncertainty.

    The safety margin is the conventional 95% uncertainty of the robust centre
    estimate using non-overlapping five-row observations. It is added to the
    configured round-trip cost. No held-out outcome is consulted.
    """
    series = pd.to_numeric(pd.Series(list(training_returns)), errors="coerce").dropna()
    independent = series.iloc[::HORIZON_ROWS]
    if independent.empty:
        return {
            "threshold_pct": float(round_trip_cost_pct),
            "cost_pct": float(round_trip_cost_pct),
            "safety_margin_pct": 0.0,
            "effective_training_samples": 0,
            "method": "cost_only_no_training_sample",
        }
    median = float(independent.median())
    mad = float((independent - median).abs().median())
    robust_sigma = 1.4826 * mad
    safety = 1.96 * robust_sigma / math.sqrt(max(1, len(independent)))
    return {
        "threshold_pct": float(round_trip_cost_pct + safety),
        "cost_pct": float(round_trip_cost_pct),
        "safety_margin_pct": float(safety),
        "effective_training_samples": int(len(independent)),
        "method": "training_only_95pct_robust_centre_uncertainty_plus_cost",
    }


def _wilson_interval(successes: int, total: int) -> list[float] | None:
    if total < 20:
        return None
    z = 1.959963984540054
    probability = successes / total
    denominator = 1.0 + z * z / total
    centre = (probability + z * z / (2.0 * total)) / denominator
    margin = z * math.sqrt(
        probability * (1.0 - probability) / total + z * z / (4.0 * total * total)
    ) / denominator
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def _make_evaluation_frame(
    *,
    dates: pd.Series,
    source_tickers: pd.Series,
    features: pd.DataFrame,
    actual: pd.Series,
    predicted: np.ndarray,
    fold_number: int,
    uncertainty_pct: float | None,
    predicted_signals: np.ndarray | None = None,
    probability_up: np.ndarray | None = None,
) -> pd.DataFrame:
    """Build only the columns used by the current gates and experiment metrics."""
    feature_rows = features.reset_index(drop=True)
    regimes = [assess_market_regime(feature_rows.iloc[index]) for index in range(len(feature_rows))]
    predicted_values = np.asarray(predicted, dtype=float)
    signals = (
        np.asarray(predicted_signals, dtype=int)
        if predicted_signals is not None
        else np.sign(predicted_values).astype(int)
    )
    frame = pd.DataFrame(
        {
            "prediction_date": pd.to_datetime(dates).dt.strftime("%Y-%m-%d").to_numpy(),
            "source_ticker": source_tickers.astype(str).to_numpy(),
            "predicted_value": predicted_values,
            "predicted_signal": signals,
            "actual_future_result": pd.to_numeric(actual, errors="coerce").to_numpy(),
            "evaluation_window": int(fold_number),
            "prediction_uncertainty_pct": uncertainty_pct,
            "is_actionable_signal": (
                np.abs(predicted_values) >= float(uncertainty_pct)
                if uncertainty_pct is not None and uncertainty_pct > 0
                else signals != 0
            ),
            "is_regime_trade_allowed": [bool(item["new_position_allowed"]) for item in regimes],
            "market_regime_position_multiplier": [
                float(item["position_size_multiplier"]) for item in regimes
            ],
        }
    )
    if probability_up is not None:
        frame["probability_up"] = np.asarray(probability_up, dtype=float)
    return frame


def _economic_replay(frame: pd.DataFrame) -> dict[str, Any]:
    """Evaluate five non-overlapping long/short signal paths after current costs."""
    if frame.empty:
        return {
            "path_count": 0,
            "median_after_cost_return_pct": None,
            "worst_max_drawdown_pct": None,
            "turnover": None,
        }
    paths: list[dict[str, float | int]] = []
    group_column = "source_ticker" if "source_ticker" in frame.columns else None
    groups = frame.groupby(group_column, sort=False) if group_column else [("", frame)]
    total_turnover = 0.0
    total_rows = 0
    for ticker, ticker_frame in groups:
        ticker_frame = ticker_frame.sort_values("prediction_date").reset_index(drop=True)
        for offset in range(HORIZON_ROWS):
            path = ticker_frame.iloc[offset::HORIZON_ROWS].copy()
            if path.empty:
                continue
            signals = pd.to_numeric(path["predicted_signal"], errors="coerce").fillna(0).clip(-1, 1)
            actionable = path["is_actionable_signal"].astype(bool)
            regime_allowed = path["is_regime_trade_allowed"].astype(bool)
            positions = signals.where(actionable & regime_allowed, 0.0)
            turnover_units = positions.diff().abs().fillna(positions.abs())
            actual = pd.to_numeric(path["actual_future_result"], errors="coerce").fillna(0.0)
            net_returns = positions * actual - turnover_units * PROMOTION_EXECUTION_COST_PCT
            wealth = 1.0
            peak = 1.0
            max_drawdown = 0.0
            for value in net_returns:
                wealth *= max(0.0, 1.0 + float(value) / 100.0)
                peak = max(peak, wealth)
                max_drawdown = min(max_drawdown, wealth / peak - 1.0)
            paths.append(
                {
                    "ticker": str(ticker),
                    "offset": offset,
                    "observations": int(len(path)),
                    "after_cost_return_pct": (wealth - 1.0) * 100.0,
                    "average_after_cost_signal_return_pct": float(net_returns.mean()),
                    "max_drawdown_pct": max_drawdown * 100.0,
                    "turnover_units": float(turnover_units.sum()),
                }
            )
            total_turnover += float(turnover_units.sum())
            total_rows += int(len(path))
    return {
        "path_count": len(paths),
        "median_after_cost_return_pct": (
            float(pd.Series([item["after_cost_return_pct"] for item in paths]).median())
            if paths else None
        ),
        "median_after_cost_signal_return_pct": (
            float(pd.Series([
                item["average_after_cost_signal_return_pct"] for item in paths
            ]).median())
            if paths else None
        ),
        "worst_max_drawdown_pct": (
            min(float(item["max_drawdown_pct"]) for item in paths) if paths else None
        ),
        "turnover": total_turnover / total_rows if total_rows else None,
        "path_metrics": paths,
        "note": "Symmetric long/short signal proxy; not a funded Virtual Trader account replay.",
    }


def summarize_evaluation(
    evaluation: pd.DataFrame,
    *,
    brier_override: float | None = None,
) -> dict[str, Any]:
    """Score one strictly OOS stream with predictive, gate, and trading metrics."""
    frame = evaluation.copy()
    frame["actual_future_result"] = pd.to_numeric(
        frame["actual_future_result"], errors="coerce"
    )
    frame["predicted_value"] = pd.to_numeric(frame["predicted_value"], errors="coerce")
    frame = frame.dropna(subset=["actual_future_result", "predicted_value"])
    actual_up = (frame["actual_future_result"] > 0).astype(int)
    predicted_up = (frame["predicted_value"] > 0).astype(int)
    direction_hits = actual_up == predicted_up
    actual_up_rate = float(actual_up.mean()) if len(frame) else 0.0
    direction_accuracy = float(direction_hits.mean()) if len(frame) else 0.0
    majority_accuracy = max(actual_up_rate, 1.0 - actual_up_rate)
    balanced = (
        float(balanced_accuracy_score(actual_up, predicted_up)) if len(frame) else 0.0
    )
    mcc = (
        float(matthews_corrcoef(actual_up, predicted_up))
        if actual_up.nunique() > 1 and predicted_up.nunique() > 1
        else 0.0
    )
    signal = pd.to_numeric(frame["predicted_signal"], errors="coerce").fillna(0).astype(int)
    actionable = frame["is_actionable_signal"].astype(bool) & frame[
        "is_regime_trade_allowed"
    ].astype(bool) & signal.ne(0)
    buy = actionable & signal.gt(0)
    sell = actionable & signal.lt(0)
    buy_success = int((frame.loc[buy, "actual_future_result"] > 0).sum())
    sell_success = int((frame.loc[sell, "actual_future_result"] < 0).sum())
    fold_accuracy = [
        float(
            (
                (fold["actual_future_result"] > 0)
                == (fold["predicted_value"] > 0)
            ).mean()
        )
        for _, fold in frame.groupby("evaluation_window", sort=True)
    ]
    quality_gate = ModelLifecycleService._walk_forward_quality_gate(frame)
    trading_gate = ModelLifecycleService._historical_trading_quality_gate(frame, TARGET)
    validation_score = direction_accuracy
    economic = _economic_replay(frame)
    brier = brier_override
    if brier is None and "probability_up" in frame.columns:
        probability = pd.to_numeric(frame["probability_up"], errors="coerce")
        valid = probability.notna()
        if valid.any():
            brier = float(brier_score_loss(actual_up[valid], probability[valid].clip(0, 1)))
    return {
        "sample_count": int(len(frame)),
        "effective_non_overlapping_sample_count": int(math.ceil(len(frame) / HORIZON_ROWS)),
        "direction_accuracy": direction_accuracy,
        "direction_accuracy_95pct_ci": _wilson_interval(int(direction_hits.sum()), len(frame)),
        "balanced_accuracy": balanced,
        "mcc": mcc,
        "actual_up_rate": actual_up_rate,
        "majority_baseline_accuracy": majority_accuracy,
        "majority_baseline_edge": direction_accuracy - majority_accuracy,
        "brier_score": brier,
        "mae_pct": float(mean_absolute_error(
            frame["actual_future_result"], frame["predicted_value"]
        )),
        "prediction_actual_correlation": (
            float(frame["predicted_value"].corr(frame["actual_future_result"]))
            if len(frame) > 1
            and frame["predicted_value"].nunique() > 1
            and frame["actual_future_result"].nunique() > 1
            else None
        ),
        "fold_direction_accuracy": fold_accuracy,
        "fold_accuracy_min": min(fold_accuracy) if fold_accuracy else None,
        "fold_accuracy_std": float(np.std(fold_accuracy)) if fold_accuracy else None,
        "actionable_predictions": int(actionable.sum()),
        "buy_predictions": int(buy.sum()),
        "sell_predictions": int(sell.sum()),
        "buy_precision": float(buy_success / buy.sum()) if buy.sum() else None,
        "buy_precision_95pct_ci": _wilson_interval(buy_success, int(buy.sum())),
        "sell_precision": float(sell_success / sell.sum()) if sell.sum() else None,
        "sell_precision_95pct_ci": _wilson_interval(sell_success, int(sell.sum())),
        "average_realized_return_after_buy_pct": (
            float(frame.loc[buy, "actual_future_result"].mean()) if buy.any() else None
        ),
        "average_underlying_return_after_sell_pct": (
            float(frame.loc[sell, "actual_future_result"].mean()) if sell.any() else None
        ),
        "average_signed_return_after_sell_pct": (
            float(-frame.loc[sell, "actual_future_result"].mean()) if sell.any() else None
        ),
        "after_cost_replay": economic,
        "validation_score": validation_score,
        "validation_score_passed": validation_score >= PRODUCTION_MIN_SCORE,
        "walk_forward_gate": quality_gate,
        "trading_gate": trading_gate,
        "clears_existing_behavioral_gates": bool(
            validation_score >= PRODUCTION_MIN_SCORE
            and quality_gate.get("passed")
            and trading_gate.get("passed")
        ),
    }


def _validation_splits(date_series: pd.Series, row_count: int) -> list[tuple[np.ndarray, np.ndarray]]:
    return _purged_date_splits(
        date_series,
        split_count=_choose_time_series_splits(row_count),
        gap_rows=HORIZON_ROWS,
    )


def _calibrate_fold_regression_threshold(
    *,
    base_pipeline: Any,
    x_train: pd.DataFrame,
    y_train: pd.Series,
    train_dates: pd.Series,
    candidate_features: list[str],
    design: str,
) -> float | None:
    normalized_dates = pd.to_datetime(train_dates).dt.normalize()
    unique_dates = pd.Index(normalized_dates.unique()).sort_values()
    calibration_start = max(20, int(len(unique_dates) * 0.80))
    inner_end = calibration_start - HORIZON_ROWS
    if inner_end < 30 or calibration_start >= len(unique_dates):
        return None
    inner_dates = unique_dates[:inner_end]
    calibration_dates = unique_dates[calibration_start:]
    inner_mask = normalized_dates.isin(inner_dates).to_numpy()
    calibration_mask = normalized_dates.isin(calibration_dates).to_numpy()
    selected, _ = select_training_features(
        x_train.iloc[inner_mask], candidate_features, design
    )
    pipeline = clone(base_pipeline)
    pipeline.fit(x_train.iloc[inner_mask][selected], y_train.iloc[inner_mask])
    predictions = pipeline.predict(x_train.iloc[calibration_mask][selected])
    return _calibrate_regression_abstention_threshold(
        y_train.iloc[calibration_mask].astype(float).to_numpy(),
        np.asarray(predictions, dtype=float),
    )


def evaluate_regression_design(
    dataset: pd.DataFrame,
    *,
    design: str,
    model_name: str,
) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    """Evaluate one feature design/algorithm without persisting a fitted model."""
    stationary = prepare_stationary_feature_dataset(dataset)
    x_frame, y_series, dates, tickers, candidates = _build_feature_frame(stationary, TARGET)
    splits = _validation_splits(dates, len(x_frame))
    base_pipeline = _build_regressor_pipeline(model_name)
    evaluation_parts: list[pd.DataFrame] = []
    feature_decisions: list[dict[str, Any]] = []
    for fold_number, (train_index, test_index) in enumerate(splits, start=1):
        x_train = x_frame.iloc[train_index]
        y_train = y_series.iloc[train_index].astype(float)
        selected, decisions = select_training_features(
            x_train, candidates, design
        )
        for decision in decisions:
            feature_decisions.append({"fold": fold_number, **decision})
        threshold = _calibrate_fold_regression_threshold(
            base_pipeline=base_pipeline,
            x_train=x_train,
            y_train=y_train,
            train_dates=dates.iloc[train_index],
            candidate_features=candidates,
            design=design,
        )
        pipeline = clone(base_pipeline)
        pipeline.fit(x_train[selected], y_train)
        predictions = pipeline.predict(x_frame.iloc[test_index][selected])
        evaluation_parts.append(
            _make_evaluation_frame(
                dates=dates.iloc[test_index],
                source_tickers=tickers.iloc[test_index],
                features=x_frame.iloc[test_index][selected],
                actual=y_series.iloc[test_index].astype(float),
                predicted=np.asarray(predictions, dtype=float),
                fold_number=fold_number,
                uncertainty_pct=threshold,
            )
        )
    evaluation = pd.concat(evaluation_parts, ignore_index=True)
    summary = summarize_evaluation(evaluation)
    summary["feature_count_median"] = float(pd.Series([
        sum(bool(item["kept"]) for item in feature_decisions if item["fold"] == fold)
        for fold in sorted({int(item["fold"]) for item in feature_decisions})
    ]).median())
    return evaluation, summary, feature_decisions


def _probability_threshold_from_inner_holdout(
    x_train: pd.DataFrame,
    y_return_train: pd.Series,
    train_dates: pd.Series,
    candidates: list[str],
    design: str,
) -> float:
    """Calibrate two-stage directional confidence on a purged inner holdout."""
    normalized_dates = pd.to_datetime(train_dates).dt.normalize()
    unique_dates = pd.Index(normalized_dates.unique()).sort_values()
    calibration_start = max(20, int(len(unique_dates) * 0.80))
    inner_end = calibration_start - HORIZON_ROWS
    if inner_end < 30 or calibration_start >= len(unique_dates):
        return 0.5
    inner_mask = normalized_dates.isin(unique_dates[:inner_end]).to_numpy()
    calibration_mask = normalized_dates.isin(unique_dates[calibration_start:]).to_numpy()
    selected, _ = select_training_features(x_train.iloc[inner_mask], candidates, design)
    labels = (y_return_train.iloc[inner_mask] > 0).astype(int)
    if labels.nunique() < 2:
        return 0.5
    pipeline = _build_classifier_pipeline("logistic_regression")
    pipeline.fit(x_train.iloc[inner_mask][selected], labels)
    probability_up = pipeline.predict_proba(x_train.iloc[calibration_mask][selected])[:, 1]
    actual = y_return_train.iloc[calibration_mask].astype(float).to_numpy()
    confidence = np.maximum(probability_up, 1.0 - probability_up)
    candidate_thresholds = sorted({
        0.5,
        *(float(np.quantile(confidence, q)) for q in (0.25, 0.40, 0.55, 0.70)),
    })
    minimum_signals = max(10, int(math.ceil(len(actual) * 0.20)))
    best: tuple[float, float] | None = None
    for threshold in candidate_thresholds:
        active = confidence >= threshold
        if int(active.sum()) < minimum_signals:
            continue
        predicted_up = probability_up[active] >= 0.5
        actual_up = actual[active] > 0
        balanced = float(balanced_accuracy_score(actual_up.astype(int), predicted_up.astype(int)))
        signed_after_cost = (
            np.where(predicted_up, 1.0, -1.0) * actual[active]
            - ROUND_TRIP_COST_PCT
        )
        score = balanced + 0.10 * float(np.tanh(signed_after_cost.mean() / 2.0))
        candidate = (score, -threshold)
        if best is None or candidate > best:
            best = candidate
    return -best[1] if best else 0.5


def evaluate_target_design(
    dataset: pd.DataFrame,
    *,
    target_design: str,
    feature_design: str = "compact",
) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    """Compare binary, economic three-class, and two-stage targets."""
    clean_target = str(target_design).strip().lower()
    if clean_target not in {"binary_direction", "economic_three_class", "two_stage"}:
        raise ValueError(f"Unsupported target design: {target_design}")
    stationary = prepare_stationary_feature_dataset(dataset)
    x_frame, y_return, dates, tickers, candidates = _build_feature_frame(stationary, TARGET)
    splits = _validation_splits(dates, len(x_frame))
    evaluation_parts: list[pd.DataFrame] = []
    threshold_evidence: list[dict[str, Any]] = []
    multiclass_brier_parts: list[tuple[float, int]] = []

    for fold_number, (train_index, test_index) in enumerate(splits, start=1):
        x_train = x_frame.iloc[train_index]
        returns_train = y_return.iloc[train_index].astype(float)
        selected, _ = select_training_features(x_train, candidates, feature_design)
        x_test = x_frame.iloc[test_index][selected]
        actual_test = y_return.iloc[test_index].astype(float)
        class_threshold = derive_economic_class_threshold(returns_train)
        threshold_evidence.append({"fold": fold_number, **class_threshold})

        if clean_target == "economic_three_class":
            threshold = float(class_threshold["threshold_pct"])
            labels = pd.Series(
                np.where(
                    returns_train > threshold,
                    1,
                    np.where(returns_train < -threshold, -1, 0),
                ),
                index=returns_train.index,
            )
            if labels.nunique() < 2:
                signals = np.full(len(x_test), int(labels.iloc[-1]))
                probability_up = np.full(len(x_test), float(signals[0] > 0))
                brier_value = None
            else:
                classifier = _build_classifier_pipeline("logistic_regression")
                classifier.fit(x_train[selected], labels)
                signals = classifier.predict(x_test).astype(int)
                probabilities = classifier.predict_proba(x_test)
                classes = classifier.classes_.astype(int)
                probability_up = (
                    probabilities[:, list(classes).index(1)]
                    if 1 in classes else np.zeros(len(x_test))
                )
                actual_labels = np.where(
                    actual_test.to_numpy() > threshold,
                    1,
                    np.where(actual_test.to_numpy() < -threshold, -1, 0),
                )
                one_hot = np.column_stack([actual_labels == item for item in classes]).astype(float)
                brier_value = float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1)))
                multiclass_brier_parts.append((brier_value, len(x_test)))
            predicted_values = signals.astype(float)
            uncertainty = None
        else:
            labels = (returns_train > 0).astype(int)
            if labels.nunique() < 2:
                probability_up = np.full(len(x_test), float(labels.iloc[-1]))
            else:
                classifier = _build_classifier_pipeline("logistic_regression")
                classifier.fit(x_train[selected], labels)
                probability_up = classifier.predict_proba(x_test)[:, 1]
            if clean_target == "binary_direction":
                signals = np.where(probability_up >= 0.5, 1, -1)
                predicted_values = signals.astype(float)
                uncertainty = None
            else:
                probability_threshold = _probability_threshold_from_inner_holdout(
                    x_train,
                    returns_train,
                    dates.iloc[train_index],
                    candidates,
                    feature_design,
                )
                confidence = np.maximum(probability_up, 1.0 - probability_up)
                signals = np.where(
                    confidence >= probability_threshold,
                    np.where(probability_up >= 0.5, 1, -1),
                    0,
                )
                threshold_evidence[-1]["probability_threshold"] = probability_threshold
                magnitude_training = returns_train.abs()
                economically_useful = returns_train.abs() > float(class_threshold["threshold_pct"])
                magnitude_pipeline = _build_regressor_pipeline("ridge_regression")
                if int(economically_useful.sum()) >= 30:
                    magnitude_pipeline.fit(
                        x_train.loc[economically_useful, selected],
                        magnitude_training.loc[economically_useful],
                    )
                else:
                    magnitude_pipeline.fit(x_train[selected], magnitude_training)
                magnitude = np.maximum(0.0, magnitude_pipeline.predict(x_test))
                predicted_values = signals * magnitude
                uncertainty = None

        evaluation_parts.append(
            _make_evaluation_frame(
                dates=dates.iloc[test_index],
                source_tickers=tickers.iloc[test_index],
                features=x_test,
                actual=actual_test,
                predicted=np.asarray(predicted_values, dtype=float),
                predicted_signals=np.asarray(signals, dtype=int),
                probability_up=np.asarray(probability_up, dtype=float),
                fold_number=fold_number,
                uncertainty_pct=uncertainty,
            )
        )

    evaluation = pd.concat(evaluation_parts, ignore_index=True)
    brier_override = None
    if multiclass_brier_parts:
        brier_override = sum(value * count for value, count in multiclass_brier_parts) / sum(
            count for _, count in multiclass_brier_parts
        )
    summary = summarize_evaluation(evaluation, brier_override=brier_override)
    if clean_target in {"binary_direction", "economic_three_class"}:
        # Class labels are not percentage-return estimates. Reporting their
        # distance from a percentage target as MAE/correlation would be a unit
        # error and could make a classifier look like a magnitude model.
        summary["mae_pct"] = None
        summary["prediction_actual_correlation"] = None
        summary["magnitude_metric_note"] = (
            "Not applicable: this configuration predicts a class, not return magnitude."
        )
    summary["economic_threshold_pct_median"] = float(pd.Series([
        item["threshold_pct"] for item in threshold_evidence
    ]).median())
    if any("probability_threshold" in item for item in threshold_evidence):
        summary["probability_threshold_median"] = float(pd.Series([
            item["probability_threshold"]
            for item in threshold_evidence
            if "probability_threshold" in item
        ]).median())
    return evaluation, summary, threshold_evidence


def evaluate_simple_baselines(dataset: pd.DataFrame) -> list[dict[str, Any]]:
    """Evaluate time-safe simple predictors on the exact model OOS windows."""
    stationary = prepare_stationary_feature_dataset(dataset)
    x_frame, y_return, dates, tickers, _ = _build_feature_frame(stationary, TARGET)
    splits = _validation_splits(dates, len(x_frame))
    pieces: dict[str, list[pd.DataFrame]] = defaultdict(list)
    for fold_number, (train_index, test_index) in enumerate(splits, start=1):
        y_train = y_return.iloc[train_index].astype(float)
        actual = y_return.iloc[test_index].astype(float)
        actual_up_rate = float((y_train > 0).mean())
        baseline_predictions = {
            "always_up": np.ones(len(test_index)),
            "training_majority": np.full(len(test_index), 1.0 if actual_up_rate >= 0.5 else -1.0),
            "zero_return": np.zeros(len(test_index)),
            "recent_5d_momentum": pd.to_numeric(
                x_frame.iloc[test_index]["return_5d_pct"], errors="coerce"
            ).fillna(0.0).to_numpy(),
            "momentum_20d": pd.to_numeric(
                x_frame.iloc[test_index]["return_20d_pct"], errors="coerce"
            ).fillna(0.0).to_numpy(),
            "sma_50_trend": pd.to_numeric(
                x_frame.iloc[test_index]["close_vs_sma_50_pct"], errors="coerce"
            ).fillna(0.0).to_numpy(),
            "matured_historical_mean": np.full(len(test_index), float(y_train.mean())),
        }
        for name, predictions in baseline_predictions.items():
            pieces[name].append(
                _make_evaluation_frame(
                    dates=dates.iloc[test_index],
                    source_tickers=tickers.iloc[test_index],
                    features=x_frame.iloc[test_index],
                    actual=actual,
                    predicted=np.asarray(predictions, dtype=float),
                    fold_number=fold_number,
                    uncertainty_pct=None,
                )
            )
    return [
        {"baseline": name, "metrics": summarize_evaluation(pd.concat(frames, ignore_index=True))}
        for name, frames in pieces.items()
    ]


def evaluate_pooled_regression(
    datasets: dict[str, pd.DataFrame],
    *,
    model_name: str,
    design: str = "compact",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fit a pooled model with globally chronological dates and no ticker encoding."""
    prepared: list[pd.DataFrame] = []
    for ticker, dataset in datasets.items():
        frame = prepare_stationary_feature_dataset(dataset).copy()
        frame["ticker"] = ticker
        prepared.append(frame)
    pooled = pd.concat(prepared, ignore_index=True).sort_values(["date", "ticker"])
    x_frame, y_return, dates, tickers, candidates = _build_feature_frame(pooled, TARGET)
    splits = _validation_splits(dates, len(x_frame))
    base_pipeline = _build_regressor_pipeline(model_name)
    evaluations: list[pd.DataFrame] = []
    for fold_number, (train_index, test_index) in enumerate(splits, start=1):
        x_train = x_frame.iloc[train_index]
        y_train = y_return.iloc[train_index].astype(float)
        selected, _ = select_training_features(x_train, candidates, design)
        threshold = _calibrate_fold_regression_threshold(
            base_pipeline=base_pipeline,
            x_train=x_train,
            y_train=y_train,
            train_dates=dates.iloc[train_index],
            candidate_features=candidates,
            design=design,
        )
        pipeline = clone(base_pipeline)
        pipeline.fit(x_train[selected], y_train)
        predictions = pipeline.predict(x_frame.iloc[test_index][selected])
        evaluations.append(
            _make_evaluation_frame(
                dates=dates.iloc[test_index],
                source_tickers=tickers.iloc[test_index],
                features=x_frame.iloc[test_index][selected],
                actual=y_return.iloc[test_index].astype(float),
                predicted=np.asarray(predictions, dtype=float),
                fold_number=fold_number,
                uncertainty_pct=threshold,
            )
        )
    evaluation = pd.concat(evaluations, ignore_index=True).sort_values(
        ["prediction_date", "source_ticker"]
    ).reset_index(drop=True)
    summary = summarize_evaluation(evaluation)
    summary["pooled_ticker_count"] = len(datasets)
    summary["global_chronology"] = True
    summary["ticker_identity_encoded"] = False
    summary["preprocessing_fit_scope"] = "training_fold_only"
    return evaluation, summary


def exact_window_comparison(
    pooled_evaluation: pd.DataFrame,
    per_ticker_evaluations: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    """Compare pooled and individual predictions on their shared OOS dates."""
    pooled_parts: list[pd.DataFrame] = []
    individual_parts: list[pd.DataFrame] = []
    rows_by_ticker: dict[str, int] = {}
    for ticker, individual in per_ticker_evaluations.items():
        pooled = pooled_evaluation[
            pooled_evaluation["source_ticker"].astype(str) == str(ticker)
        ].copy()
        shared = sorted(set(pooled["prediction_date"]) & set(individual["prediction_date"]))
        pooled = pooled[pooled["prediction_date"].isin(shared)]
        individual = individual[individual["prediction_date"].isin(shared)]
        rows_by_ticker[str(ticker)] = min(len(pooled), len(individual))
        pooled_parts.append(pooled)
        individual_parts.append(individual)
    pooled_shared = pd.concat(pooled_parts, ignore_index=True)
    individual_shared = pd.concat(individual_parts, ignore_index=True)
    return {
        "shared_rows_by_ticker": rows_by_ticker,
        "pooled": summarize_evaluation(pooled_shared),
        "per_ticker": summarize_evaluation(individual_shared),
    }


def _feature_decision_report(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in decisions:
        grouped[str(decision["feature"])].append(decision)
    report: list[dict[str, Any]] = []
    for feature in sorted(grouped):
        rows = grouped[feature]
        correlations = [
            float(row["absolute_correlation"])
            for row in rows
            if row.get("absolute_correlation") is not None
        ]
        correlation_partners = Counter(
            str(row["correlated_with"])
            for row in rows
            if row.get("correlated_with")
        )
        reasons = Counter(str(row["reason"]) for row in rows)
        kept = sum(bool(row["kept"]) for row in rows)
        report.append(
            {
                "feature": feature,
                "purpose": feature_purpose(feature),
                "fold_observations": len(rows),
                "kept_folds": kept,
                "removed_folds": len(rows) - kept,
                "kept_rate": kept / len(rows),
                "removal_reasons": dict(reasons),
                "most_common_redundant_with": (
                    correlation_partners.most_common(1)[0][0]
                    if correlation_partners else None
                ),
                "median_absolute_redundancy_correlation": (
                    float(pd.Series(correlations).median()) if correlations else None
                ),
                "max_absolute_redundancy_correlation": max(correlations) if correlations else None,
            }
        )
    return report


def _mean(values: Iterable[Any]) -> float | None:
    series = pd.to_numeric(pd.Series(list(values), dtype="object"), errors="coerce").dropna()
    return float(series.mean()) if not series.empty else None


def aggregate_records(
    records: list[dict[str, Any]],
    group_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[tuple(str(record.get(field, "unknown")) for field in group_fields)].append(record)
    output: list[dict[str, Any]] = []
    for keys, rows in sorted(groups.items()):
        metrics = [dict(row["metrics"]) for row in rows]
        output.append(
            {
                **dict(zip(group_fields, keys)),
                "runs": len(rows),
                "behavioral_gate_passes": sum(
                    bool(item.get("clears_existing_behavioral_gates")) for item in metrics
                ),
                "mean_direction_accuracy": _mean(item.get("direction_accuracy") for item in metrics),
                "mean_balanced_accuracy": _mean(item.get("balanced_accuracy") for item in metrics),
                "mean_mcc": _mean(item.get("mcc") for item in metrics),
                "mean_majority_baseline_edge": _mean(
                    item.get("majority_baseline_edge") for item in metrics
                ),
                "mean_mae_pct": _mean(item.get("mae_pct") for item in metrics),
                "mean_prediction_actual_correlation": _mean(
                    item.get("prediction_actual_correlation") for item in metrics
                ),
                "mean_brier_score": _mean(item.get("brier_score") for item in metrics),
                "total_actionable_predictions": sum(
                    int(item.get("actionable_predictions") or 0) for item in metrics
                ),
                "mean_buy_precision": _mean(item.get("buy_precision") for item in metrics),
                "mean_sell_precision": _mean(item.get("sell_precision") for item in metrics),
                "mean_after_cost_replay_pct": _mean(
                    (item.get("after_cost_replay") or {}).get("median_after_cost_return_pct")
                    for item in metrics
                ),
                "worst_drawdown_pct": min(
                    (
                        float((item.get("after_cost_replay") or {}).get("worst_max_drawdown_pct"))
                        for item in metrics
                        if (item.get("after_cost_replay") or {}).get("worst_max_drawdown_pct") is not None
                    ),
                    default=None,
                ),
                "mean_turnover": _mean(
                    (item.get("after_cost_replay") or {}).get("turnover") for item in metrics
                ),
            }
        )
    return output


def paired_cluster_bootstrap(
    challenger: list[dict[str, Any]],
    reference: list[dict[str, Any]],
    *,
    metric: str,
    key_fields: tuple[str, ...],
    iterations: int = 2000,
) -> dict[str, Any]:
    """Bootstrap paired OOS metric differences by ticker-period cluster."""
    def index(records: list[dict[str, Any]]) -> dict[tuple[str, ...], float]:
        output: dict[tuple[str, ...], float] = {}
        for row in records:
            value = row["metrics"].get(metric)
            if isinstance(value, (int, float)) and np.isfinite(float(value)):
                output[tuple(str(row.get(field, "")) for field in key_fields)] = float(value)
        return output

    challenger_index = index(challenger)
    reference_index = index(reference)
    shared = sorted(set(challenger_index) & set(reference_index))
    if len(shared) < 5:
        return {"paired_samples": len(shared), "mean_difference": None, "95pct_ci": None}
    differences = np.asarray(
        [challenger_index[key] - reference_index[key] for key in shared], dtype=float
    )
    rng = np.random.default_rng(42)
    samples = np.asarray([
        float(rng.choice(differences, size=len(differences), replace=True).mean())
        for _ in range(iterations)
    ])
    return {
        "paired_samples": len(shared),
        "mean_difference": float(differences.mean()),
        "95pct_ci": [float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))],
        "statistically_clear_at_95pct": bool(
            float(np.quantile(samples, 0.025)) > 0
            or float(np.quantile(samples, 0.975)) < 0
        ),
        "resampling_unit": "paired_ticker_period_algorithm" if "algorithm" in key_fields else "paired_ticker_period",
    }


def _initial_training_strata(dataset: pd.DataFrame) -> dict[str, float | None]:
    """Measure volatility/liquidity on the initial 60%, before final OOS windows."""
    ordered = dataset.sort_values("date").reset_index(drop=True)
    training = ordered.iloc[: max(30, int(len(ordered) * 0.60))]
    returns = pd.to_numeric(training.get("return_1d_pct"), errors="coerce")
    dollar_volume = (
        pd.to_numeric(training.get("close"), errors="coerce")
        * pd.to_numeric(training.get("volume"), errors="coerce")
    )
    return {
        "initial_training_annualized_volatility_pct": (
            float(returns.std() * math.sqrt(252.0)) if returns.notna().any() else None
        ),
        "initial_training_median_dollar_volume": (
            float(dollar_volume.median()) if dollar_volume.notna().any() else None
        ),
    }


def _assign_tertiles(values: dict[str, float | None]) -> dict[str, str]:
    valid = pd.Series({key: value for key, value in values.items() if value is not None}).sort_values()
    if valid.empty:
        return {key: "unavailable" for key in values}
    ranks = valid.rank(method="first", pct=True)
    bands = {
        key: "low" if rank <= 1 / 3 else "medium" if rank <= 2 / 3 else "high"
        for key, rank in ranks.items()
    }
    return {key: bands.get(key, "unavailable") for key in values}


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def _artifact_directory(root: Path, sample: SecuritySample, period: str, model: str) -> Path:
    return model_security_root(root, sample.market, sample.ticker) / period / TARGET / model


def _stage1_schedule() -> list[tuple[SecuritySample, str]]:
    schedule = [
        (sample, period)
        for sample in REPRESENTATIVE_UNIVERSE
        for period in ("2y", "5y")
    ]
    schedule.extend((sample, "10y") for sample in EXPERIMENT_COHORT)
    return schedule


def _stage1_group(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for key in sorted({str(row.get(field, "unknown")) for row in rows}):
        group = [row for row in rows if str(row.get(field, "unknown")) == key]
        output[key] = {
            "trained": len(group),
            "validation_score_passed": sum(
                float(row.get("validation_score") or 0.0) >= PRODUCTION_MIN_SCORE for row in group
            ),
            "walk_forward_gate_passed": sum(
                bool(row.get("walk_forward_quality_passed")) for row in group
            ),
            "trading_gate_passed": sum(
                bool(row.get("historical_trading_quality_passed")) for row in group
            ),
            "current_provenance": sum(
                bool(row.get("validation_provenance_current")) for row in group
            ),
            "fully_current_scheme_validated": sum(bool(row.get("passed")) for row in group),
        }
    return output


def _load_current_evaluation(
    models_root: Path,
    sample: SecuritySample,
    period: str,
    algorithm: str,
) -> pd.DataFrame:
    path = _artifact_directory(models_root, sample, period, algorithm) / "evaluation_table.csv"
    frame = pd.read_csv(path)
    if "source_ticker" not in frame.columns:
        frame["source_ticker"] = sample.ticker
    if "predicted_signal" not in frame.columns:
        frame["predicted_signal"] = np.sign(
            pd.to_numeric(frame["predicted_value"], errors="coerce").fillna(0.0)
        ).astype(int)
    return frame


def run_stage1(
    output_root: Path,
) -> tuple[
    dict[str, Any],
    dict[tuple[str, str, str], pd.DataFrame],
    list[dict[str, Any]],
    dict[tuple[str, str, str, str], pd.DataFrame],
]:
    """Run/resume the broader benchmark using unchanged production training code."""
    models_root = output_root / "stage1_current_scheme_models"
    datasets: dict[tuple[str, str, str], pd.DataFrame] = {}
    failures: list[dict[str, str]] = []
    trained_now = skipped_existing = 0
    strata_raw: dict[tuple[str, str, str], dict[str, float | None]] = {}

    for sample, period in _stage1_schedule():
        key = (sample.market, sample.ticker, period)
        try:
            benchmark = "2800" if sample.market == "HK" else "VOO"
            dataset = build_feature_dataset(
                sample.ticker,
                period=period,
                benchmark=benchmark,
                include_news_sentiment=False,
                market=sample.market,
            )
            datasets[key] = dataset
            strata_raw[key] = _initial_training_strata(dataset)
        except Exception as exc:  # noqa: BLE001 - report provider failures per security
            failures.append({
                "market": sample.market,
                "ticker": sample.ticker,
                "period": period,
                "step": "dataset",
                "error": str(exc),
            })
            logger.exception("Dataset failed market=%s ticker=%s period=%s", sample.market, sample.ticker, period)
            continue

        for algorithm in CURRENT_ALGORITHMS:
            artifact_dir = _artifact_directory(models_root, sample, period, algorithm)
            metrics_path = artifact_dir / "metrics_summary.json"
            evaluation_path = artifact_dir / "evaluation_table.csv"
            valid_existing = False
            if metrics_path.exists() and evaluation_path.exists():
                try:
                    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                    valid_existing = (
                        int(metrics.get("validation_scheme_version") or 0) >= VALIDATION_SCHEME_VERSION
                        and str(metrics.get("target_price_source"))
                        == "adjusted_close_with_raw_close_fallback"
                    )
                except (OSError, ValueError, TypeError):
                    valid_existing = False
            if valid_existing:
                skipped_existing += 1
                continue
            try:
                train_baseline_model(
                    dataset_df=dataset,
                    ticker=sample.ticker,
                    period=period,
                    target_name=TARGET,
                    task_type="regression",
                    model_name=algorithm,
                    output_dir=models_root,
                    market=sample.market,
                    publish_canonical=True,
                )
                trained_now += 1
            except Exception as exc:  # noqa: BLE001 - preserve complete failure evidence
                failures.append({
                    "market": sample.market,
                    "ticker": sample.ticker,
                    "period": period,
                    "algorithm": algorithm,
                    "step": "training",
                    "error": str(exc),
                })
                logger.exception(
                    "Training failed market=%s ticker=%s period=%s model=%s",
                    sample.market,
                    sample.ticker,
                    period,
                    algorithm,
                )

    audit = audit_saved_models(models_root, target_name=TARGET)
    rows = list(audit.get("passing_models") or []) + list(audit.get("strongest_failed_models") or [])
    sample_lookup = {(item.market, item.ticker): item for item in REPRESENTATIVE_UNIVERSE}
    volatility_bands: dict[tuple[str, str, str], str] = {}
    liquidity_bands: dict[tuple[str, str, str], str] = {}
    for period in MODEL_PERIODS:
        period_keys = [key for key in strata_raw if key[2] == period]
        vol = _assign_tertiles({
            "|".join(key): strata_raw[key]["initial_training_annualized_volatility_pct"]
            for key in period_keys
        })
        liq = _assign_tertiles({
            "|".join(key): strata_raw[key]["initial_training_median_dollar_volume"]
            for key in period_keys
        })
        for key in period_keys:
            volatility_bands[key] = vol["|".join(key)]
            liquidity_bands[key] = liq["|".join(key)]
    for row in rows:
        key = (str(row["market"]), str(row["ticker"]), str(row["period"]))
        sample = sample_lookup.get((key[0], key[1]))
        row["asset_class"] = sample.asset_class if sample else row.get("ticker_class", "unknown")
        row["volatility_band"] = volatility_bands.get(key, "unavailable")
        row["liquidity_band"] = liquidity_bands.get(key, "unavailable")
        row.update(strata_raw.get(key, {}))

    current_records: list[dict[str, Any]] = []
    current_evaluations: dict[tuple[str, str, str, str], pd.DataFrame] = {}
    for sample in EXPERIMENT_COHORT:
        for period in MODEL_PERIODS:
            key = (sample.market, sample.ticker, period)
            if key not in datasets:
                continue
            for algorithm in CURRENT_ALGORITHMS:
                try:
                    evaluation = _load_current_evaluation(models_root, sample, period, algorithm)
                except (OSError, pd.errors.ParserError):
                    continue
                current_evaluations[(sample.market, sample.ticker, period, algorithm)] = evaluation
                current_records.append({
                    "market": sample.market,
                    "ticker": sample.ticker,
                    "asset_class": sample.asset_class,
                    "period": period,
                    "algorithm": algorithm,
                    "feature_design": "current",
                    "target_design": "exact_return_regression",
                    "status": "EXPERIMENTAL",
                    "runtime_selectable": False,
                    "metrics": summarize_evaluation(evaluation),
                })

    stage1 = {
        "sample_definition": [item.__dict__ for item in REPRESENTATIVE_UNIVERSE],
        "period_policy": {
            "2y": "all representative securities",
            "5y": "all representative securities",
            "10y": "fixed experiment cohort with sufficient rows",
        },
        "market_cap_limitation": (
            "The production research dataset has no point-in-time historical market-cap field. "
            "No current market-cap snapshot was backfilled into historical folds. Historical "
            "median dollar volume from the initial 60% is reported as the supported liquidity proxy."
        ),
        "trained_in_this_invocation": trained_now,
        "resumed_existing_isolated_artifacts": skipped_existing,
        "training_failures": failures,
        "models_trained": len(rows),
        "models_fully_current_scheme_validated": sum(bool(row.get("passed")) for row in rows),
        "full_validation_pass_rate": (
            sum(bool(row.get("passed")) for row in rows) / len(rows) if rows else 0.0
        ),
        "audit_status_counts": dict(Counter(str(row.get("audit_status")) for row in rows)),
        "validation_funnel": audit.get("validation_funnel"),
        "failure_reasons": audit.get("failure_reasons"),
        "by_algorithm": _stage1_group(rows, "model_name"),
        "by_market": _stage1_group(rows, "market"),
        "by_asset_class": _stage1_group(rows, "asset_class"),
        "by_period": _stage1_group(rows, "period"),
        "by_volatility_band": _stage1_group(rows, "volatility_band"),
        "by_liquidity_band": _stage1_group(rows, "liquidity_band"),
        "strongest_models": sorted(
            rows,
            key=lambda row: (
                float(row.get("balanced_direction_accuracy") or 0.0),
                float(row.get("direction_edge") or -1.0),
            ),
            reverse=True,
        )[:20],
        "all_model_rows": rows,
    }
    return stage1, datasets, current_records, current_evaluations


def run_design_experiments(
    *,
    datasets: dict[tuple[str, str, str], pd.DataFrame],
    current_records: list[dict[str, Any]],
    current_evaluations: dict[tuple[str, str, str, str], pd.DataFrame],
) -> dict[str, Any]:
    feature_records = list(current_records)
    feature_evaluations = dict(current_evaluations)
    feature_decisions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    experiment_failures: list[dict[str, str]] = []
    baseline_records: list[dict[str, Any]] = []
    target_records: list[dict[str, Any]] = []

    for sample in EXPERIMENT_COHORT:
        for period in MODEL_PERIODS:
            dataset = datasets.get((sample.market, sample.ticker, period))
            if dataset is None:
                continue
            try:
                for baseline in evaluate_simple_baselines(dataset):
                    baseline_records.append({
                        "market": sample.market,
                        "ticker": sample.ticker,
                        "asset_class": sample.asset_class,
                        "period": period,
                        **baseline,
                    })
            except Exception as exc:  # noqa: BLE001
                experiment_failures.append({
                    "market": sample.market,
                    "ticker": sample.ticker,
                    "period": period,
                    "configuration": "baselines",
                    "error": str(exc),
                })
                logger.exception("Baseline evaluation failed %s %s %s", sample.market, sample.ticker, period)

            for design in ("reduced", "compact"):
                for algorithm in CURRENT_ALGORITHMS:
                    try:
                        evaluation, metrics, decisions = evaluate_regression_design(
                            dataset,
                            design=design,
                            model_name=algorithm,
                        )
                        record = {
                            "market": sample.market,
                            "ticker": sample.ticker,
                            "asset_class": sample.asset_class,
                            "period": period,
                            "algorithm": algorithm,
                            "feature_design": design,
                            "target_design": "exact_return_regression",
                            "status": "EXPERIMENTAL",
                            "runtime_selectable": False,
                            "metrics": metrics,
                        }
                        feature_records.append(record)
                        feature_evaluations[(sample.market, sample.ticker, period, algorithm, design)] = evaluation
                        feature_decisions[design].extend([
                            {
                                "market": sample.market,
                                "ticker": sample.ticker,
                                "asset_class": sample.asset_class,
                                "period": period,
                                "algorithm": algorithm,
                                **decision,
                            }
                            for decision in decisions
                        ])
                    except Exception as exc:  # noqa: BLE001
                        experiment_failures.append({
                            "market": sample.market,
                            "ticker": sample.ticker,
                            "period": period,
                            "configuration": f"{design}:{algorithm}",
                            "error": str(exc),
                        })
                        logger.exception(
                            "Feature experiment failed %s %s %s %s %s",
                            sample.market,
                            sample.ticker,
                            period,
                            design,
                            algorithm,
                        )

            for target_design in (
                "binary_direction",
                "economic_three_class",
                "two_stage",
            ):
                try:
                    _, metrics, evidence = evaluate_target_design(
                        dataset,
                        target_design=target_design,
                        feature_design="compact",
                    )
                    target_records.append({
                        "market": sample.market,
                        "ticker": sample.ticker,
                        "asset_class": sample.asset_class,
                        "period": period,
                        "algorithm": (
                            "logistic_plus_ridge" if target_design == "two_stage" else "logistic_regression"
                        ),
                        "feature_design": "compact",
                        "target_design": target_design,
                        "status": "EXPERIMENTAL",
                        "runtime_selectable": False,
                        "training_only_threshold_evidence": evidence,
                        "metrics": metrics,
                    })
                except Exception as exc:  # noqa: BLE001
                    experiment_failures.append({
                        "market": sample.market,
                        "ticker": sample.ticker,
                        "period": period,
                        "configuration": f"target:{target_design}",
                        "error": str(exc),
                    })
                    logger.exception(
                        "Target experiment failed %s %s %s %s",
                        sample.market,
                        sample.ticker,
                        period,
                        target_design,
                    )

    pooled_records: list[dict[str, Any]] = []
    pooled_comparisons: list[dict[str, Any]] = []
    pooled_groups: dict[tuple[str, str], list[SecuritySample]] = defaultdict(list)
    for sample in EXPERIMENT_COHORT:
        pooled_groups[(sample.market, sample.asset_class)].append(sample)
    for (market, asset_class), samples in sorted(pooled_groups.items()):
        if len(samples) < 3:
            continue
        for period in MODEL_PERIODS:
            group_datasets = {
                sample.ticker: datasets[(market, sample.ticker, period)]
                for sample in samples
                if (market, sample.ticker, period) in datasets
            }
            if len(group_datasets) < 3:
                continue
            for algorithm in CURRENT_ALGORITHMS:
                try:
                    evaluation, metrics = evaluate_pooled_regression(
                        group_datasets,
                        model_name=algorithm,
                        design="compact",
                    )
                    record = {
                        "market": market,
                        "ticker": "POOLED",
                        "asset_class": asset_class,
                        "period": period,
                        "algorithm": algorithm,
                        "feature_design": "compact_pooled",
                        "target_design": "exact_return_regression",
                        "status": "EXPERIMENTAL",
                        "runtime_selectable": False,
                        "members": sorted(group_datasets),
                        "metrics": metrics,
                    }
                    pooled_records.append(record)
                    individual = {
                        sample.ticker: feature_evaluations[
                            (market, sample.ticker, period, algorithm, "compact")
                        ]
                        for sample in samples
                        if (market, sample.ticker, period, algorithm, "compact")
                        in feature_evaluations
                    }
                    if len(individual) == len(group_datasets):
                        pooled_comparisons.append({
                            "market": market,
                            "asset_class": asset_class,
                            "period": period,
                            "algorithm": algorithm,
                            **exact_window_comparison(evaluation, individual),
                        })
                except Exception as exc:  # noqa: BLE001
                    experiment_failures.append({
                        "market": market,
                        "asset_class": asset_class,
                        "period": period,
                        "configuration": f"pooled:{algorithm}",
                        "error": str(exc),
                    })
                    logger.exception(
                        "Pooled experiment failed %s %s %s %s",
                        market,
                        asset_class,
                        period,
                        algorithm,
                    )

    current = [row for row in feature_records if row["feature_design"] == "current"]
    reduced = [row for row in feature_records if row["feature_design"] == "reduced"]
    compact = [row for row in feature_records if row["feature_design"] == "compact"]
    paired_feature_tests: dict[str, Any] = {}
    for design, records in (("reduced", reduced), ("compact", compact)):
        paired_feature_tests[design] = {
            metric: paired_cluster_bootstrap(
                records,
                current,
                metric=metric,
                key_fields=("market", "ticker", "period", "algorithm"),
            )
            for metric in (
                "direction_accuracy",
                "balanced_accuracy",
                "majority_baseline_edge",
            )
        }

    compact_ridge = [
        row for row in compact if row.get("algorithm") == "ridge_regression"
    ]
    target_significance: dict[str, Any] = {}
    for target_design in ("binary_direction", "economic_three_class", "two_stage"):
        records = [row for row in target_records if row["target_design"] == target_design]
        target_significance[target_design] = {
            metric: paired_cluster_bootstrap(
                records,
                compact_ridge,
                metric=metric,
                key_fields=("market", "ticker", "period"),
            )
            for metric in (
                "direction_accuracy",
                "balanced_accuracy",
                "majority_baseline_edge",
            )
        }

    baseline_for_aggregation = [
        {
            **{key: value for key, value in row.items() if key != "baseline"},
            "algorithm": row["baseline"],
        }
        for row in baseline_records
    ]
    all_experiment_metrics = feature_records + target_records + pooled_records
    cleared = [
        row for row in all_experiment_metrics
        if bool(row["metrics"].get("clears_existing_behavioral_gates"))
    ]
    return {
        "experiment_cohort": [item.__dict__ for item in EXPERIMENT_COHORT],
        "isolation": {
            "status": "EXPERIMENTAL",
            "runtime_selectable": False,
            "models_persisted": False,
            "production_model_directory_written": False,
            "lifecycle_registry_written": False,
            "active_model_pointer_changed": False,
        },
        "feature_configuration_summary": aggregate_records(
            feature_records, ("feature_design", "algorithm")
        ),
        "feature_by_period": aggregate_records(
            feature_records, ("feature_design", "period")
        ),
        "feature_by_market": aggregate_records(
            feature_records, ("feature_design", "market")
        ),
        "feature_by_asset_class": aggregate_records(
            feature_records, ("feature_design", "asset_class")
        ),
        "feature_decision_report": {
            design: _feature_decision_report(rows)
            for design, rows in feature_decisions.items()
        },
        "paired_feature_significance": paired_feature_tests,
        "simple_baselines": aggregate_records(
            baseline_for_aggregation, ("algorithm", "period")
        ),
        "target_configuration_summary": aggregate_records(
            target_records, ("target_design",)
        ),
        "target_by_period": aggregate_records(
            target_records, ("target_design", "period")
        ),
        "paired_target_significance_vs_compact_ridge": target_significance,
        "pooled_summary": aggregate_records(
            pooled_records, ("market", "asset_class", "algorithm", "period")
        ),
        "pooled_exact_window_comparisons": pooled_comparisons,
        "experiments_clearing_existing_behavioral_gates": cleared,
        "experiment_failures": experiment_failures,
        "raw_feature_records": feature_records,
        "raw_target_records": target_records,
        "raw_pooled_records": pooled_records,
    }


def _best_configuration(experiments: dict[str, Any]) -> dict[str, Any] | None:
    candidates = list(experiments.get("feature_configuration_summary") or [])
    candidates += list(experiments.get("target_configuration_summary") or [])
    candidates += list(experiments.get("pooled_summary") or [])
    if not candidates:
        return None
    least_bad = max(
        candidates,
        key=lambda row: (
            float(row.get("mean_balanced_accuracy") or 0.0),
            float(row.get("mean_mcc") or 0.0),
            float(row.get("mean_majority_baseline_edge") or -1.0),
        ),
    )
    genuine = [
        row for row in candidates
        if float(row.get("mean_majority_baseline_edge") or -1.0) > 0
        and float(row.get("mean_balanced_accuracy") or 0.0) > 0.50
        and float(row.get("mean_mcc") or 0.0) > 0
    ]
    return {
        "genuine_repeatable_edge_found": bool(genuine),
        "best_supported_configuration": (
            max(
                genuine,
                key=lambda row: (
                    float(row.get("mean_balanced_accuracy") or 0.0),
                    float(row.get("mean_mcc") or 0.0),
                ),
            )
            if genuine else None
        ),
        "least_bad_by_balanced_accuracy": least_bad,
        "interpretation": (
            "No configuration is called best when its aggregate majority-baseline "
            "edge is non-positive or its balanced accuracy/MCC does not show real edge."
        ),
    }


def build_final_report(output_root: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "EXPERIMENTAL",
        "runtime_selectable": False,
        "production_deployment": False,
        "validation_thresholds_modified": False,
        "active_model_pointers_modified": False,
        "output_root": str(output_root.resolve()),
    }
    _write_json(output_root / "EXPERIMENT_MANIFEST.json", manifest)
    stage1, datasets, current_records, current_evaluations = run_stage1(output_root)
    _write_json(output_root / "stage1_broader_benchmark.json", stage1)
    experiments = run_design_experiments(
        datasets=datasets,
        current_records=current_records,
        current_evaluations=current_evaluations,
    )
    _write_json(output_root / "stage2_stage3_experiments.json", experiments)
    best = _best_configuration(experiments)
    any_gate_clear = bool(experiments.get("experiments_clearing_existing_behavioral_gates"))
    report = {
        "manifest": manifest,
        "1_broader_benchmark_results": stage1,
        "2_current_feature_model_performance": [
            row for row in experiments["feature_configuration_summary"]
            if row.get("feature_design") == "current"
        ],
        "3_reduced_feature_performance": [
            row for row in experiments["feature_configuration_summary"]
            if row.get("feature_design") == "reduced"
        ],
        "4_compact_feature_performance": [
            row for row in experiments["feature_configuration_summary"]
            if row.get("feature_design") == "compact"
        ],
        "5_per_ticker_versus_pooled": experiments["pooled_exact_window_comparisons"],
        "6_regression_vs_classification_two_stage": {
            "target_summary": experiments["target_configuration_summary"],
            "by_period": experiments["target_by_period"],
        },
        "7_best_performing_configuration": best,
        "8_performance_against_simple_baselines": experiments["simple_baselines"],
        "9_existing_quality_gates": {
            "stage1_fully_current_scheme_validated": stage1[
                "models_fully_current_scheme_validated"
            ],
            "experimental_behavioral_gate_clear_count": len(
                experiments["experiments_clearing_existing_behavioral_gates"]
            ),
            "any_experimental_configuration_clears_behavioral_gates": any_gate_clear,
            "note": (
                "An isolated experiment can clear behavioral calculations but is not lifecycle-registered, "
                "promoted, CURRENTLY_VALIDATED for runtime, or selectable by Virtual Trader."
            ),
        },
        "10_statistical_significance": {
            "feature_design": experiments["paired_feature_significance"],
            "target_design": experiments["paired_target_significance_vs_compact_ridge"],
        },
        "11_files_changed": [
            ".gitignore",
            "scripts/model_design_benchmark.py",
            "tests/test_model_design_benchmark.py",
        ],
        "12_tests_run": "Populated in the delivery summary after commands actually run.",
        "13_recommendation": "Determined from evidence in the delivery summary; no automatic adoption.",
        "feature_quality_details": experiments["feature_decision_report"],
        "experiment_failures": experiments["experiment_failures"],
        "raw_experiment_records": {
            "feature": experiments["raw_feature_records"],
            "target": experiments["raw_target_records"],
            "pooled": experiments["raw_pooled_records"],
        },
    }
    _write_json(output_root / "final_model_design_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="data/model_design_experiments/controlled_2026_08",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    report = build_final_report(Path(args.output_dir))
    print(json.dumps({
        "report": str((Path(args.output_dir) / "final_model_design_report.json").resolve()),
        "stage1_models": report["1_broader_benchmark_results"]["models_trained"],
        "stage1_passed": report["1_broader_benchmark_results"][
            "models_fully_current_scheme_validated"
        ],
        "experiment_gate_clear_count": report["9_existing_quality_gates"][
            "experimental_behavioral_gate_clear_count"
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
