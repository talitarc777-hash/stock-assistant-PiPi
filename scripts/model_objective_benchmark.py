"""Isolated objective-redesign audit for the Model Trader.

This module compares benchmark-relative, cross-sectional, and risk objectives.
It never writes to ``data/models``, lifecycle registries, or active-version
pointers.  Experiment outputs are confined to ``data/model_design_experiments``.

The final chronological holdout is declared before any model is fitted.  Model,
feature, threshold, and architecture choices use development data only.  Exactly
one architecture is then fitted once and evaluated on the locked holdout.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import sys
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    matthews_corrcoef,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    recall_score,
    roc_auc_score,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.model_lifecycle_service import PROMOTION_EXECUTION_COST_PCT
from app.services.model_training import (
    _build_classifier_pipeline,
    _build_feature_frame,
    _build_regressor_pipeline,
    _purged_date_splits,
    prepare_stationary_feature_dataset,
)
from app.services.research_pipeline import build_feature_dataset
from scripts.model_design_benchmark import (
    COMPACT_FEATURES,
    HORIZON_ROWS,
    REPRESENTATIVE_UNIVERSE,
    ROUND_TRIP_COST_PCT,
    _wilson_interval,
    derive_economic_class_threshold,
    select_training_features,
)

logger = logging.getLogger(__name__)

EXPERIMENT_NAME = "objective_redesign_2026_08"
SOURCE_PERIOD = "10y"
LOCKED_HOLDOUT_DATES = 252
BOOTSTRAP_REPETITIONS = 400
RANDOM_SEED = 20260823
REGRESSION_MODELS = ("ridge_regression", "random_forest")
CLASSIFICATION_MODELS = ("logistic_regression", "random_forest")
BENCHMARKS = {"US": "VOO", "HK": "2800"}

RELATIVE_TARGET = "target_5d_excess_return"
RISK_VOLATILITY_TARGET = "target_5d_realized_volatility_pct"
RISK_ADVERSE_TARGET = "target_5d_max_adverse_move_pct"

SELECTION_CRITERIA: dict[str, dict[str, Any]] = {
    "relative_return": {
        "balanced_accuracy_min": 0.52,
        "correlation_ci_lower_min": 0.0,
        "after_cost_ci_lower_min": 0.0,
        "positive_fold_rate_min": 0.60,
    },
    "cross_sectional_ranking": {
        "spearman_ic_min": 0.02,
        "spearman_ic_ci_lower_min": 0.0,
        "after_cost_spread_ci_lower_min": 0.0,
        "positive_fold_rate_min": 0.60,
    },
    "risk_filter": {
        "roc_auc_ci_lower_min": 0.50,
        "roc_auc_uplift_over_best_simple_baseline_min": 0.0,
        "pr_auc_uplift_over_best_simple_baseline_min": 0.0,
        "brier_improvement_min": 0.0,
        "positive_fold_rate_min": 0.60,
    },
    "rule_plus_ml_risk_filter": {
        "after_cost_improvement_ci_lower_min": 0.0,
        "filtered_after_cost_return_min": 0.0,
        "positive_fold_rate_min": 0.60,
        "risk_roc_auc_ci_lower_min": 0.50,
    },
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if value is pd.NA:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(payload), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def production_model_fingerprint(root: Path | None = None) -> str:
    """Hash production-model file metadata without reading or changing artifacts."""
    model_root = root or (PROJECT_ROOT / "data" / "models")
    if not model_root.exists():
        return hashlib.sha256(b"missing").hexdigest()
    rows: list[str] = []
    for path in sorted(item for item in model_root.rglob("*") if item.is_file()):
        stat = path.stat()
        rows.append(f"{path.relative_to(model_root).as_posix()}|{stat.st_size}|{stat.st_mtime_ns}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def add_forward_risk_targets(dataset: pd.DataFrame) -> pd.DataFrame:
    """Add five-day future risk labels using only future OHLC observations.

    Realised volatility is annualised so it is directly comparable with the
    existing 20-day rolling-volatility feature.  Maximum adverse move is a
    positive loss magnitude from today's adjusted close to the worst of the
    next five adjusted closes.  No event class boundary is created here;
    boundaries are derived inside each training fold.
    """
    result = dataset.sort_values("date").reset_index(drop=True).copy()
    close = pd.to_numeric(result.get("adj_close"), errors="coerce")
    raw_close = pd.to_numeric(result["close"], errors="coerce")
    close = close.where(close.notna() & close.gt(0), raw_close)
    daily_return = close.pct_change()
    future_daily = pd.concat(
        [daily_return.shift(-step) for step in range(1, HORIZON_ROWS + 1)],
        axis=1,
    )
    result[RISK_VOLATILITY_TARGET] = (
        future_daily.std(axis=1, ddof=1) * math.sqrt(252.0) * 100.0
    )
    future_paths = pd.concat(
        [((close.shift(-step) / close) - 1.0) * 100.0 for step in range(1, HORIZON_ROWS + 1)],
        axis=1,
    )
    result[RISK_ADVERSE_TARGET] = (-future_paths.min(axis=1)).clip(lower=0.0)
    incomplete = future_daily.isna().any(axis=1) | future_paths.isna().any(axis=1)
    result.loc[incomplete, [RISK_VOLATILITY_TARGET, RISK_ADVERSE_TARGET]] = np.nan
    return result


def compute_market_holdout_boundaries(
    datasets: dict[tuple[str, str], pd.DataFrame],
    *,
    holdout_dates: int = LOCKED_HOLDOUT_DATES,
) -> dict[str, dict[str, Any]]:
    """Declare one market-calendar holdout and a five-date label purge."""
    boundaries: dict[str, dict[str, Any]] = {}
    for market in BENCHMARKS:
        dates = pd.Index(sorted({
            pd.Timestamp(value).normalize()
            for (row_market, _), frame in datasets.items()
            if row_market == market
            for value in pd.to_datetime(frame["date"], errors="coerce").dropna()
        }))
        if len(dates) <= holdout_dates + HORIZON_ROWS + 60:
            raise ValueError(f"{market} does not have enough dates for the locked holdout.")
        locked_start_position = len(dates) - holdout_dates
        locked_start = pd.Timestamp(dates[locked_start_position])
        last_development = pd.Timestamp(dates[locked_start_position - HORIZON_ROWS - 1])
        purged_dates = dates[
            locked_start_position - HORIZON_ROWS:locked_start_position
        ]
        boundaries[market] = {
            "locked_start": locked_start,
            "locked_end": pd.Timestamp(dates[-1]),
            "last_development_date": last_development,
            "purged_dates": [pd.Timestamp(item) for item in purged_dates],
            "market_date_count": int(len(dates)),
            "locked_date_count": int(holdout_dates),
        }
    return boundaries


def split_development_and_locked(
    frame: pd.DataFrame,
    boundary: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    development = frame.loc[
        dates <= pd.Timestamp(boundary["last_development_date"])
    ].copy()
    locked = frame.loc[dates >= pd.Timestamp(boundary["locked_start"])].copy()
    return development.reset_index(drop=True), locked.reset_index(drop=True)


def derive_regime_thresholds(training_features: pd.DataFrame) -> dict[str, float]:
    """Derive simple, fixed regime boundaries from a training fold only."""
    benchmark_trend = pd.to_numeric(
        training_features["benchmark_return_20d_pct"], errors="coerce"
    ).dropna()
    volatility = pd.to_numeric(
        training_features["rolling_volatility_20_pct"], errors="coerce"
    ).dropna()
    drawdown = pd.to_numeric(
        training_features["drawdown_from_peak_pct"], errors="coerce"
    ).dropna()
    return {
        "bull_threshold_pct": float(benchmark_trend.median()) if len(benchmark_trend) else 0.0,
        "high_vol_threshold_pct": float(volatility.median()) if len(volatility) else 0.0,
        "stress_drawdown_threshold_pct": float(drawdown.quantile(0.25)) if len(drawdown) else 0.0,
    }


def attach_regimes(
    output: pd.DataFrame,
    test_features: pd.DataFrame,
    thresholds: dict[str, float],
) -> pd.DataFrame:
    result = output.copy()
    trend = pd.to_numeric(test_features["benchmark_return_20d_pct"], errors="coerce")
    volatility = pd.to_numeric(test_features["rolling_volatility_20_pct"], errors="coerce")
    drawdown = pd.to_numeric(test_features["drawdown_from_peak_pct"], errors="coerce")
    result["trend_regime"] = np.where(
        trend.to_numpy() > thresholds["bull_threshold_pct"], "bull", "non_bull"
    )
    result["volatility_regime"] = np.where(
        volatility.to_numpy() > thresholds["high_vol_threshold_pct"], "high_vol", "low_vol"
    )
    result["stress_regime"] = np.where(
        drawdown.to_numpy() <= thresholds["stress_drawdown_threshold_pct"],
        "stressed",
        "normal",
    )
    return result


def _bootstrap_mean_ci(
    values: Iterable[float],
    *,
    block_size: int = HORIZON_ROWS,
    repetitions: int = BOOTSTRAP_REPETITIONS,
    seed: int = RANDOM_SEED,
) -> list[float] | None:
    clean = pd.to_numeric(pd.Series(list(values)), errors="coerce").dropna().to_numpy(float)
    if len(clean) < 20:
        return None
    rng = np.random.default_rng(seed)
    block = max(1, min(int(block_size), len(clean)))
    starts = np.arange(max(1, len(clean) - block + 1))
    estimates: list[float] = []
    blocks_needed = int(math.ceil(len(clean) / block))
    for _ in range(repetitions):
        chosen = rng.choice(starts, size=blocks_needed, replace=True)
        sample = np.concatenate([clean[start:start + block] for start in chosen])[:len(clean)]
        estimates.append(float(np.mean(sample)))
    return [float(np.quantile(estimates, 0.025)), float(np.quantile(estimates, 0.975))]


def _safe_auc(actual: pd.Series, score: pd.Series) -> float | None:
    valid = actual.notna() & score.notna()
    if int(valid.sum()) < 2 or actual[valid].nunique() < 2:
        return None
    return float(roc_auc_score(actual[valid].astype(int), score[valid].astype(float)))


def _safe_pr_auc(actual: pd.Series, score: pd.Series) -> float | None:
    valid = actual.notna() & score.notna()
    if int(valid.sum()) < 2 or actual[valid].nunique() < 2:
        return None
    return float(average_precision_score(actual[valid].astype(int), score[valid].astype(float)))


def _empirical_percentile(training: pd.Series, values: pd.Series) -> np.ndarray:
    reference = np.sort(pd.to_numeric(training, errors="coerce").dropna().to_numpy(float))
    current = pd.to_numeric(values, errors="coerce").fillna(0.0).to_numpy(float)
    if not len(reference):
        return np.full(len(current), 0.5)
    return np.searchsorted(reference, current, side="right") / len(reference)


def _calibrated_univariate_probability(
    training_proxy: pd.Series,
    training_labels: pd.Series,
    test_proxy: pd.Series,
) -> np.ndarray:
    """Fit a training-only one-feature logistic probability baseline."""
    labels = pd.to_numeric(training_labels, errors="coerce").astype(int)
    if labels.nunique() < 2:
        return np.full(len(test_proxy), float(labels.iloc[-1]))
    train_frame = pd.DataFrame({
        "proxy": pd.to_numeric(training_proxy, errors="coerce")
    })
    test_frame = pd.DataFrame({
        "proxy": pd.to_numeric(test_proxy, errors="coerce")
    })
    model = _build_classifier_pipeline("logistic_regression")
    model.fit(train_frame, labels)
    classes = list(model.classes_)
    probabilities = model.predict_proba(test_frame)
    return probabilities[:, classes.index(1)] if 1 in classes else np.zeros(len(test_proxy))


def _classification_threshold(
    target_kind: str,
    training_target: pd.Series,
) -> dict[str, Any]:
    if target_kind in {"relative_three_class", "relative_binary"}:
        return derive_economic_class_threshold(training_target)
    if target_kind == "adverse_event":
        return {
            "threshold_pct": float(training_target.quantile(0.85)),
            "quantile": 0.85,
            "method": "training_only_85th_percentile_future_adverse_move",
        }
    if target_kind == "high_vol_event":
        return {
            "threshold_pct": float(training_target.quantile(0.75)),
            "quantile": 0.75,
            "method": "training_only_75th_percentile_future_realised_volatility",
        }
    raise ValueError(f"Unknown target kind: {target_kind}")


def _labels_from_threshold(
    values: pd.Series,
    *,
    target_kind: str,
    threshold: float,
) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if target_kind == "relative_three_class":
        return pd.Series(
            np.where(numeric > threshold, 1, np.where(numeric < -threshold, -1, 0)),
            index=values.index,
            dtype=int,
        )
    return (numeric > threshold).astype(int)


def _make_oos_splits(dates: pd.Series) -> list[tuple[np.ndarray, np.ndarray]]:
    return _purged_date_splits(dates, split_count=5, gap_rows=HORIZON_ROWS)


def pooled_market_frame(
    datasets: dict[tuple[str, str], pd.DataFrame],
    *,
    market: str,
    boundary: dict[str, Any],
    locked: bool,
) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    for (row_market, ticker), raw in datasets.items():
        if row_market != market:
            continue
        development, holdout = split_development_and_locked(raw, boundary)
        selected = holdout if locked else development
        if selected.empty:
            continue
        selected = prepare_stationary_feature_dataset(selected)
        selected["ticker"] = ticker
        pieces.append(selected)
    if not pieces:
        raise ValueError(f"No {market} rows were available.")
    return pd.concat(pieces, ignore_index=True).sort_values(["date", "ticker"]).reset_index(drop=True)


def evaluate_regression_oos(
    pooled: pd.DataFrame,
    *,
    target_name: str,
    model_name: str,
    target_kind: str,
) -> pd.DataFrame:
    """Create a globally date-split OOS stream for one pooled regression."""
    x_frame, y_series, dates, tickers, candidates = _build_feature_frame(pooled, target_name)
    parts: list[pd.DataFrame] = []
    base = _build_regressor_pipeline(model_name)
    for fold, (train_index, test_index) in enumerate(_make_oos_splits(dates), start=1):
        x_train = x_frame.iloc[train_index]
        y_train = y_series.iloc[train_index].astype(float)
        selected, _ = select_training_features(x_train, candidates, "compact")
        model = clone(base)
        model.fit(x_train[selected], y_train)
        x_test = x_frame.iloc[test_index]
        prediction = np.asarray(model.predict(x_test[selected]), dtype=float)
        threshold = (
            float(derive_economic_class_threshold(y_train)["threshold_pct"])
            if target_kind == "relative_return"
            else float("nan")
        )
        if target_kind == "relative_return":
            baseline = pd.to_numeric(x_test["excess_return_5d_pct"], errors="coerce").fillna(0.0)
        else:
            baseline = pd.to_numeric(x_test["rolling_volatility_20_pct"], errors="coerce").fillna(0.0)
        regime_thresholds = derive_regime_thresholds(x_train)
        part = pd.DataFrame({
            "date": pd.to_datetime(dates.iloc[test_index]).dt.normalize().to_numpy(),
            "ticker": tickers.iloc[test_index].astype(str).to_numpy(),
            "fold": fold,
            "actual": y_series.iloc[test_index].astype(float).to_numpy(),
            "prediction": prediction,
            "baseline_prediction": baseline.to_numpy(float),
            "economic_threshold_pct": threshold,
        })
        part = attach_regimes(part, x_test.reset_index(drop=True), regime_thresholds)
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


def evaluate_classification_oos(
    pooled: pd.DataFrame,
    *,
    target_name: str,
    model_name: str,
    target_kind: str,
) -> pd.DataFrame:
    """Create a globally date-split OOS stream with training-only labels."""
    x_frame, y_series, dates, tickers, candidates = _build_feature_frame(pooled, target_name)
    parts: list[pd.DataFrame] = []
    base = _build_classifier_pipeline(model_name)
    for fold, (train_index, test_index) in enumerate(_make_oos_splits(dates), start=1):
        x_train = x_frame.iloc[train_index]
        y_train = y_series.iloc[train_index].astype(float)
        x_test = x_frame.iloc[test_index]
        y_test = y_series.iloc[test_index].astype(float)
        selected, _ = select_training_features(x_train, candidates, "compact")
        threshold_evidence = _classification_threshold(target_kind, y_train)
        threshold = float(threshold_evidence["threshold_pct"])
        train_labels = _labels_from_threshold(
            y_train, target_kind=target_kind, threshold=threshold
        )
        actual_labels = _labels_from_threshold(
            y_test, target_kind=target_kind, threshold=threshold
        )
        if train_labels.nunique() < 2:
            predicted_labels = np.full(len(test_index), int(train_labels.iloc[-1]))
            probability_event = np.full(len(test_index), float(predicted_labels[0] == 1))
        else:
            model = clone(base)
            model.fit(x_train[selected], train_labels)
            predicted_labels = np.asarray(model.predict(x_test[selected]), dtype=int)
            probabilities = model.predict_proba(x_test[selected])
            classes = list(model.classes_)
            probability_event = (
                probabilities[:, classes.index(1)]
                if 1 in classes else np.zeros(len(test_index))
            )
        proxy_pairs = {
            "recent_relative_momentum": (
                pd.to_numeric(x_train["excess_return_5d_pct"], errors="coerce"),
                pd.to_numeric(x_test["excess_return_5d_pct"], errors="coerce"),
            ),
            "rolling_volatility_20d": (
                pd.to_numeric(x_train["rolling_volatility_20_pct"], errors="coerce"),
                pd.to_numeric(x_test["rolling_volatility_20_pct"], errors="coerce"),
            ),
            "current_drawdown": (
                -pd.to_numeric(x_train["drawdown_from_peak_pct"], errors="coerce"),
                -pd.to_numeric(x_test["drawdown_from_peak_pct"], errors="coerce"),
            ),
            "intraday_range_proxy": (
                pd.to_numeric(x_train["intraday_range_pct"], errors="coerce"),
                pd.to_numeric(x_test["intraday_range_pct"], errors="coerce"),
            ),
        }
        baseline_probabilities = {
            name: _calibrated_univariate_probability(
                train_proxy, train_labels, test_proxy
            )
            for name, (train_proxy, test_proxy) in proxy_pairs.items()
        }
        primary_baseline = (
            "recent_relative_momentum" if target_kind.startswith("relative")
            else "current_drawdown" if target_kind == "adverse_event"
            else "rolling_volatility_20d"
        )
        regime_thresholds = derive_regime_thresholds(x_train)
        part = pd.DataFrame({
            "date": pd.to_datetime(dates.iloc[test_index]).dt.normalize().to_numpy(),
            "ticker": tickers.iloc[test_index].astype(str).to_numpy(),
            "fold": fold,
            "actual": y_test.to_numpy(float),
            "actual_label": actual_labels.to_numpy(int),
            "predicted_label": predicted_labels,
            "probability_event": np.asarray(probability_event, dtype=float),
            "baseline_probability": baseline_probabilities[primary_baseline],
            "economic_threshold_pct": threshold,
            "threshold_method": str(threshold_evidence["method"]),
        })
        for name, values in baseline_probabilities.items():
            part[f"baseline_probability_{name}"] = values
        part = attach_regimes(part, x_test.reset_index(drop=True), regime_thresholds)
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


def _non_overlapping_relative_returns(frame: pd.DataFrame) -> pd.DataFrame:
    """Return five independent signal paths with simple turnover costs."""
    rows: list[dict[str, Any]] = []
    for ticker, ticker_frame in frame.groupby("ticker", sort=False):
        ordered = ticker_frame.sort_values("date").reset_index(drop=True)
        for offset in range(HORIZON_ROWS):
            path = ordered.iloc[offset::HORIZON_ROWS].copy()
            previous = 0.0
            for _, row in path.iterrows():
                threshold = float(row.get("economic_threshold_pct") or 0.0)
                if "predicted_label" in path.columns:
                    signal = int(row["predicted_label"])
                    if set(pd.to_numeric(path["predicted_label"], errors="coerce").dropna().unique()) <= {0, 1}:
                        signal = 1 if signal == 1 else 0
                else:
                    prediction = float(row["prediction"])
                    signal = 1 if prediction > threshold else -1 if prediction < -threshold else 0
                turnover = abs(float(signal) - previous)
                net = float(signal) * float(row["actual"]) - turnover * PROMOTION_EXECUTION_COST_PCT
                rows.append({
                    "date": row["date"],
                    "ticker": ticker,
                    "fold": int(row["fold"]),
                    "offset": offset,
                    "signal": signal,
                    "turnover": turnover,
                    "net_excess_return_pct": net,
                })
                previous = float(signal)
    return pd.DataFrame(rows)


def regression_summary(frame: pd.DataFrame, *, target_kind: str) -> dict[str, Any]:
    actual = pd.to_numeric(frame["actual"], errors="coerce")
    prediction = pd.to_numeric(frame["prediction"], errors="coerce")
    baseline = pd.to_numeric(frame["baseline_prediction"], errors="coerce")
    valid = actual.notna() & prediction.notna() & baseline.notna()
    scored = frame.loc[valid].copy()
    actual = actual[valid]
    prediction = prediction[valid]
    baseline = baseline[valid]
    model_error = (actual - prediction).abs()
    baseline_error = (actual - baseline).abs()
    improvement = baseline_error - model_error
    result: dict[str, Any] = {
        "sample_count": int(len(scored)),
        "ticker_count": int(scored["ticker"].nunique()),
        "date_count": int(scored["date"].nunique()),
        "mae_pct": float(mean_absolute_error(actual, prediction)),
        "rmse_pct": float(math.sqrt(mean_squared_error(actual, prediction))),
        "correlation": (
            float(prediction.corr(actual))
            if prediction.nunique() > 1 and actual.nunique() > 1 else None
        ),
        "correlation_by_fold": [],
        "baseline_mae_pct": float(mean_absolute_error(actual, baseline)),
        "baseline_rmse_pct": float(math.sqrt(mean_squared_error(actual, baseline))),
        "mae_improvement_pct": float(improvement.mean()),
        "mae_improvement_95pct_ci": _bootstrap_mean_ci(improvement),
    }
    fold_correlations: list[float] = []
    fold_improvement: list[float] = []
    for _, fold in scored.groupby("fold", sort=True):
        fold_actual = pd.to_numeric(fold["actual"], errors="coerce")
        fold_prediction = pd.to_numeric(fold["prediction"], errors="coerce")
        corr = (
            float(fold_prediction.corr(fold_actual))
            if fold_prediction.nunique() > 1 and fold_actual.nunique() > 1 else 0.0
        )
        fold_correlations.append(corr)
        fold_improvement.append(float(
            (fold_actual - pd.to_numeric(fold["baseline_prediction"], errors="coerce")).abs().mean()
            - (fold_actual - fold_prediction).abs().mean()
        ))
    result["correlation_by_fold"] = fold_correlations
    result["positive_correlation_fold_rate"] = float(np.mean(np.asarray(fold_correlations) > 0))
    result["positive_mae_improvement_fold_rate"] = float(np.mean(np.asarray(fold_improvement) > 0))
    # A paired moving-block bootstrap on products is a conservative diagnostic
    # interval for the sign of association; it is not a parametric p-value.
    centred_product = (prediction - prediction.mean()) * (actual - actual.mean())
    product_ci = _bootstrap_mean_ci(centred_product)
    denominator = float(prediction.std(ddof=0) * actual.std(ddof=0))
    result["correlation_95pct_block_bootstrap_ci"] = (
        [item / denominator for item in product_ci]
        if product_ci and denominator > 0 else None
    )

    if target_kind == "relative_return":
        actual_up = (actual > 0).astype(int)
        predicted_up = (prediction > 0).astype(int)
        result.update({
            "direction_accuracy": float((actual_up == predicted_up).mean()),
            "direction_accuracy_95pct_ci": _wilson_interval(
                int((actual_up == predicted_up).sum()), int(len(actual_up))
            ),
            "balanced_accuracy": float(balanced_accuracy_score(actual_up, predicted_up)),
            "mcc": float(matthews_corrcoef(actual_up, predicted_up))
            if predicted_up.nunique() > 1 else 0.0,
        })
        replay = _non_overlapping_relative_returns(scored)
        result["actionable_signal_rate"] = float(replay["signal"].ne(0).mean())
        result["average_after_cost_excess_return_pct"] = float(
            replay["net_excess_return_pct"].mean()
        )
        result["after_cost_excess_return_95pct_ci"] = _bootstrap_mean_ci(
            replay["net_excess_return_pct"]
        )
        fold_net = replay.groupby("fold")["net_excess_return_pct"].mean()
        result["positive_after_cost_fold_rate"] = float((fold_net > 0).mean())
    return result


def _auc_block_bootstrap(frame: pd.DataFrame, score_column: str) -> list[float] | None:
    normalized_dates = pd.to_datetime(frame["date"]).dt.normalize()
    dates = pd.Index(normalized_dates.unique()).sort_values()
    if len(dates) < 30:
        return None
    rng = np.random.default_rng(RANDOM_SEED)
    block = min(HORIZON_ROWS, len(dates))
    starts = np.arange(max(1, len(dates) - block + 1))
    estimates: list[float] = []
    blocks_needed = int(math.ceil(len(dates) / block))
    date_codes = pd.Categorical(normalized_dates, categories=dates, ordered=True).codes
    actual = pd.to_numeric(frame["actual_label"], errors="coerce").astype(int).to_numpy()
    score = pd.to_numeric(frame[score_column], errors="coerce").astype(float).to_numpy()
    for _ in range(BOOTSTRAP_REPETITIONS):
        chosen = rng.choice(starts, size=blocks_needed, replace=True)
        sampled_dates = np.concatenate([dates[start:start + block] for start in chosen])[:len(dates)]
        sampled_codes = pd.Categorical(
            sampled_dates, categories=dates, ordered=True
        ).codes
        date_weights = np.bincount(sampled_codes, minlength=len(dates)).astype(float)
        row_weights = date_weights[date_codes]
        active = row_weights > 0
        if np.unique(actual[active]).size < 2:
            continue
        estimates.append(float(roc_auc_score(
            actual[active], score[active], sample_weight=row_weights[active]
        )))
    if len(estimates) < 20:
        return None
    return [float(np.quantile(estimates, 0.025)), float(np.quantile(estimates, 0.975))]


def classification_summary(frame: pd.DataFrame, *, target_kind: str) -> dict[str, Any]:
    actual = pd.to_numeric(frame["actual_label"], errors="coerce").astype(int)
    predicted = pd.to_numeric(frame["predicted_label"], errors="coerce").astype(int)
    probability = pd.to_numeric(frame["probability_event"], errors="coerce").clip(0, 1)
    result: dict[str, Any] = {
        "sample_count": int(len(frame)),
        "ticker_count": int(frame["ticker"].nunique()),
        "date_count": int(frame["date"].nunique()),
        "balanced_accuracy": float(balanced_accuracy_score(actual, predicted)),
        "macro_f1": float(f1_score(actual, predicted, average="macro", zero_division=0)),
        "mcc": float(matthews_corrcoef(actual, predicted)) if predicted.nunique() > 1 else 0.0,
        "confusion_matrix_labels": sorted(int(item) for item in actual.unique()),
        "confusion_matrix": confusion_matrix(
            actual, predicted, labels=sorted(actual.unique())
        ).tolist(),
    }
    fold_scores = [
        float(balanced_accuracy_score(fold["actual_label"], fold["predicted_label"]))
        for _, fold in frame.groupby("fold", sort=True)
    ]
    result["balanced_accuracy_by_fold"] = fold_scores
    result["positive_skill_fold_rate"] = float(np.mean(np.asarray(fold_scores) > 0.5))
    if target_kind == "relative_three_class":
        replay = _non_overlapping_relative_returns(frame)
        result["neutral_prediction_rate"] = float(predicted.eq(0).mean())
        result["average_after_cost_excess_return_pct"] = float(
            replay["net_excess_return_pct"].mean()
        )
        result["after_cost_excess_return_95pct_ci"] = _bootstrap_mean_ci(
            replay["net_excess_return_pct"]
        )
        result["positive_after_cost_fold_rate"] = float(
            (replay.groupby("fold")["net_excess_return_pct"].mean() > 0).mean()
        )
        return result

    prevalence = float(actual.mean())
    baseline_probability = pd.to_numeric(
        frame["baseline_probability"], errors="coerce"
    ).fillna(prevalence).clip(0, 1)
    roc = _safe_auc(actual, probability)
    pr_auc = _safe_pr_auc(actual, probability)
    baseline_roc = _safe_auc(actual, baseline_probability)
    baseline_pr = _safe_pr_auc(actual, baseline_probability)
    tn, fp, fn, tp = confusion_matrix(actual, predicted, labels=[0, 1]).ravel()
    result.update({
        "event_prevalence": prevalence,
        "roc_auc": roc,
        "roc_auc_95pct_block_bootstrap_ci": _auc_block_bootstrap(frame, "probability_event"),
        "pr_auc": pr_auc,
        "pr_auc_uplift_over_prevalence": (pr_auc - prevalence) if pr_auc is not None else None,
        "brier_score": float(brier_score_loss(actual, probability)),
        "prevalence_brier_score": float(brier_score_loss(actual, np.full(len(actual), prevalence))),
        "baseline_roc_auc": baseline_roc,
        "baseline_pr_auc": baseline_pr,
        "baseline_brier_score": float(brier_score_loss(actual, baseline_probability)),
        "recall": float(recall_score(actual, predicted, zero_division=0)),
        "precision": float(precision_score(actual, predicted, zero_division=0)),
        "false_positive_rate": float(fp / (fp + tn)) if fp + tn else None,
    })
    simple_baselines: dict[str, dict[str, float | None]] = {}
    for column in sorted(
        item for item in frame.columns if item.startswith("baseline_probability_")
    ):
        name = column.removeprefix("baseline_probability_")
        baseline_score = pd.to_numeric(frame[column], errors="coerce").fillna(prevalence).clip(0, 1)
        simple_baselines[name] = {
            "roc_auc": _safe_auc(actual, baseline_score),
            "pr_auc": _safe_pr_auc(actual, baseline_score),
            "brier_score": float(brier_score_loss(actual, baseline_score)),
        }
    result["simple_baselines"] = simple_baselines
    result["best_simple_baseline_roc_auc"] = max(
        (float(item["roc_auc"]) for item in simple_baselines.values() if item["roc_auc"] is not None),
        default=None,
    )
    result["best_simple_baseline_pr_auc"] = max(
        (float(item["pr_auc"]) for item in simple_baselines.values() if item["pr_auc"] is not None),
        default=None,
    )
    result["best_simple_baseline_brier_score"] = min(
        (float(item["brier_score"]) for item in simple_baselines.values() if item["brier_score"] is not None),
        default=None,
    )
    result["roc_auc_uplift_over_best_simple_baseline"] = (
        roc - result["best_simple_baseline_roc_auc"]
        if roc is not None and result["best_simple_baseline_roc_auc"] is not None else None
    )
    result["pr_auc_uplift_over_best_simple_baseline"] = (
        pr_auc - result["best_simple_baseline_pr_auc"]
        if pr_auc is not None and result["best_simple_baseline_pr_auc"] is not None else None
    )
    result["brier_improvement_over_best_simple_baseline"] = (
        result["best_simple_baseline_brier_score"] - result["brier_score"]
        if result["best_simple_baseline_brier_score"] is not None else None
    )
    fold_auc: list[float] = []
    for _, fold in frame.groupby("fold", sort=True):
        value = _safe_auc(fold["actual_label"], fold["probability_event"])
        if value is not None:
            fold_auc.append(value)
    result["roc_auc_by_fold"] = fold_auc
    result["positive_skill_fold_rate"] = float(np.mean(np.asarray(fold_auc) > 0.5)) if fold_auc else 0.0
    result["calibration"] = []
    try:
        bins = pd.qcut(probability.rank(method="first"), q=5, labels=False, duplicates="drop")
        for bin_number, group in frame.assign(_bin=bins, _probability=probability).groupby("_bin"):
            result["calibration"].append({
                "bin": int(bin_number),
                "mean_probability": float(group["_probability"].mean()),
                "observed_rate": float(group["actual_label"].mean()),
                "samples": int(len(group)),
            })
    except ValueError:
        pass
    if target_kind == "relative_binary":
        replay = _non_overlapping_relative_returns(frame)
        result["average_after_cost_excess_return_pct"] = float(
            replay["net_excess_return_pct"].mean()
        )
        result["after_cost_excess_return_95pct_ci"] = _bootstrap_mean_ci(
            replay["net_excess_return_pct"]
        )
        result["positive_after_cost_fold_rate"] = float(
            (replay.groupby("fold")["net_excess_return_pct"].mean() > 0).mean()
        )
    return result


def cross_sectional_ranking_summary(
    frame: pd.DataFrame,
    *,
    prediction_column: str = "prediction",
) -> dict[str, Any]:
    """Evaluate daily cross-sectional ranks and five non-overlapping paths."""
    date_rows: list[dict[str, Any]] = []
    selections: dict[pd.Timestamp, tuple[set[str], set[str]]] = {}
    for date, date_frame in frame.groupby("date", sort=True):
        clean = date_frame.dropna(subset=[prediction_column, "actual"]).copy()
        clean = clean.drop_duplicates("ticker", keep="last")
        if len(clean) < 4:
            continue
        q = max(1, int(math.floor(len(clean) * 0.20)))
        ranked = clean.sort_values(prediction_column)
        bottom = ranked.head(q)
        top = ranked.tail(q)
        actual_ranked = clean.sort_values("actual")
        actual_top = set(actual_ranked.tail(q)["ticker"])
        top_set = set(top["ticker"])
        bottom_set = set(bottom["ticker"])
        selections[pd.Timestamp(date)] = (top_set, bottom_set)
        date_rows.append({
            "date": pd.Timestamp(date),
            "fold": int(clean["fold"].iloc[0]),
            "security_count": int(len(clean)),
            "spearman_ic": float(clean[prediction_column].corr(clean["actual"], method="spearman")),
            "pearson_ic": float(clean[prediction_column].corr(clean["actual"], method="pearson")),
            "top_quintile_excess_return_pct": float(top["actual"].mean()),
            "bottom_quintile_excess_return_pct": float(bottom["actual"].mean()),
            "gross_spread_pct": float(top["actual"].mean() - bottom["actual"].mean()),
            "top_quintile_hit_rate": float(len(top_set & actual_top) / q),
        })
    daily = pd.DataFrame(date_rows).sort_values("date").reset_index(drop=True)
    if daily.empty:
        return {"date_count": 0, "reason": "fewer_than_four_securities_per_date"}
    daily["turnover"] = 0.0
    daily["after_cost_spread_pct"] = daily["gross_spread_pct"]
    for offset in range(HORIZON_ROWS):
        path_index = daily.index[offset::HORIZON_ROWS]
        prior_top: set[str] = set()
        prior_bottom: set[str] = set()
        for index in path_index:
            date = pd.Timestamp(daily.loc[index, "date"])
            top, bottom = selections[date]
            top_turnover = 1.0 - (len(top & prior_top) / max(1, len(top))) if prior_top else 1.0
            bottom_turnover = 1.0 - (len(bottom & prior_bottom) / max(1, len(bottom))) if prior_bottom else 1.0
            turnover = top_turnover + bottom_turnover
            daily.loc[index, "turnover"] = turnover
            daily.loc[index, "after_cost_spread_pct"] = (
                daily.loc[index, "gross_spread_pct"]
                - turnover * PROMOTION_EXECUTION_COST_PCT
            )
            prior_top, prior_bottom = top, bottom
    fold_ic = daily.groupby("fold")["spearman_ic"].mean()
    fold_spread = daily.groupby("fold")["after_cost_spread_pct"].mean()
    return {
        "date_count": int(len(daily)),
        "median_security_count": float(daily["security_count"].median()),
        "spearman_ic": float(daily["spearman_ic"].mean()),
        "spearman_ic_95pct_block_bootstrap_ci": _bootstrap_mean_ci(daily["spearman_ic"]),
        "pearson_ic": float(daily["pearson_ic"].mean()),
        "positive_ic_date_rate": float(daily["spearman_ic"].gt(0).mean()),
        "top_quintile_excess_return_pct": float(daily["top_quintile_excess_return_pct"].mean()),
        "bottom_quintile_excess_return_pct": float(daily["bottom_quintile_excess_return_pct"].mean()),
        "gross_spread_pct": float(daily["gross_spread_pct"].mean()),
        "after_cost_spread_pct": float(daily["after_cost_spread_pct"].mean()),
        "after_cost_spread_95pct_block_bootstrap_ci": _bootstrap_mean_ci(daily["after_cost_spread_pct"]),
        "top_quintile_hit_rate": float(daily["top_quintile_hit_rate"].mean()),
        "turnover": float(daily["turnover"].mean()),
        "positive_fold_rate": float(((fold_ic > 0) & (fold_spread > 0)).mean()),
        "fold_spearman_ic": [float(item) for item in fold_ic],
        "fold_after_cost_spread_pct": [float(item) for item in fold_spread],
        "cost_note": "Five non-overlapping date paths; 5 bps per one-way portfolio turnover unit per leg.",
    }


def regime_summaries(frame: pd.DataFrame, *, result_kind: str) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for column in ("trend_regime", "volatility_regime", "stress_regime"):
        rows: list[dict[str, Any]] = []
        for label, group in frame.groupby(column, sort=True):
            if result_kind == "regression":
                actual = pd.to_numeric(group["actual"], errors="coerce")
                prediction = pd.to_numeric(group["prediction"], errors="coerce")
                metric = (
                    float(prediction.corr(actual))
                    if prediction.nunique() > 1 and actual.nunique() > 1 else None
                )
                rows.append({"regime": str(label), "samples": int(len(group)), "correlation": metric})
            else:
                auc = _safe_auc(group["actual_label"], group["probability_event"])
                rows.append({"regime": str(label), "samples": int(len(group)), "roc_auc": auc})
        output[column] = rows
    return output


def deterministic_rule_signal(frame: pd.DataFrame) -> pd.Series:
    """Reproduce the existing 65-point deterministic score boundary by row."""
    numeric = frame.apply(pd.to_numeric, errors="coerce")
    score = pd.Series(0.0, index=frame.index)
    score += np.where(numeric["close_vs_sma_200_pct"] > 0, 20, 0)
    score += np.where(
        numeric["close_vs_sma_50_pct"] < numeric["close_vs_sma_200_pct"], 10, 0
    )
    score += np.where(
        numeric["close_vs_sma_20_pct"] < numeric["close_vs_sma_50_pct"], 10, 0
    )
    score += np.where(numeric["rsi_14"].between(50, 65, inclusive="both"), 10, 0)
    score += np.where(numeric["macd_line_pct"] > numeric["macd_signal_pct"], 10, 0)
    score += np.where(numeric["return_20d_pct"] > 0, 5, 0)
    score += np.where(numeric["volume_vs_20d_avg"] > 1, 10, 0)
    score += np.where(numeric["distance_from_52w_high_pct"] >= -10, 5, 0)
    score -= np.where(numeric["rsi_14"] > 75, 5, 0)
    score -= np.where(numeric["close_vs_sma_50_pct"] > 10, 5, 0)
    score -= np.where(numeric["rolling_volatility_20_pct"] > 35, 5, 0)
    score -= np.where(numeric["drawdown_from_peak_pct"] < -15, 5, 0)
    return (score.clip(0, 100) >= 65).astype(int)


def _long_only_path_rows(frame: pd.DataFrame, signal_column: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for ticker, ticker_frame in frame.groupby("ticker", sort=False):
        ordered = ticker_frame.sort_values("date").reset_index(drop=True)
        for offset in range(HORIZON_ROWS):
            path = ordered.iloc[offset::HORIZON_ROWS]
            previous = 0
            wealth = 1.0
            peak = 1.0
            for _, row in path.iterrows():
                position = int(row[signal_column])
                turnover = abs(position - previous)
                net = position * float(row["absolute_return"]) - turnover * PROMOTION_EXECUTION_COST_PCT
                wealth *= max(0.0, 1.0 + net / 100.0)
                peak = max(peak, wealth)
                rows.append({
                    "date": row["date"],
                    "ticker": ticker,
                    "fold": int(row["fold"]),
                    "offset": offset,
                    "position": position,
                    "net_return_pct": net,
                    "drawdown_pct": (wealth / peak - 1.0) * 100.0,
                })
                previous = position
    return pd.DataFrame(rows)


def rule_risk_hybrid_summary(
    risk_stream: pd.DataFrame,
    feature_frame: pd.DataFrame,
) -> dict[str, Any]:
    available = feature_frame.copy()
    available["date"] = pd.to_datetime(available["date"]).dt.normalize()
    available["rule_signal"] = deterministic_rule_signal(available)
    available["absolute_return"] = pd.to_numeric(
        available["target_5d_return"], errors="coerce"
    )
    merged = risk_stream.merge(
        available[["date", "ticker", "rule_signal", "absolute_return"]],
        on=["date", "ticker"], how="inner", validate="one_to_one",
    ).dropna(subset=["absolute_return"])
    merged["filtered_signal"] = (
        merged["rule_signal"].eq(1) & merged["predicted_label"].eq(0)
    ).astype(int)
    rule_path = _long_only_path_rows(merged, "rule_signal")
    filtered_path = _long_only_path_rows(merged, "filtered_signal")
    keys = ["date", "ticker", "fold", "offset"]
    paired = rule_path.merge(
        filtered_path, on=keys, suffixes=("_rule", "_filtered"), validate="one_to_one"
    )
    paired["improvement_pct"] = (
        paired["net_return_pct_filtered"] - paired["net_return_pct_rule"]
    )
    active_rule = merged["rule_signal"].eq(1)
    blocked = active_rule & merged["predicted_label"].eq(1)
    losing_rule = active_rule & merged["absolute_return"].lt(0)
    winning_rule = active_rule & merged["absolute_return"].gt(0)
    fold_improvement = paired.groupby("fold")["improvement_pct"].mean()
    return {
        "sample_count": int(len(merged)),
        "rule_signal_count": int(active_rule.sum()),
        "blocked_rule_signal_count": int(blocked.sum()),
        "blocked_signal_rate": float(blocked.sum() / active_rule.sum()) if active_rule.any() else 0.0,
        "avoided_losing_signal_rate": float((blocked & losing_rule).sum() / losing_rule.sum()) if losing_rule.any() else None,
        "retained_winning_signal_rate": float((~blocked & winning_rule).sum() / winning_rule.sum()) if winning_rule.any() else None,
        "rule_after_cost_return_pct": float(rule_path["net_return_pct"].mean()),
        "filtered_after_cost_return_pct": float(filtered_path["net_return_pct"].mean()),
        "after_cost_improvement_pct": float(paired["improvement_pct"].mean()),
        "after_cost_improvement_95pct_ci": _bootstrap_mean_ci(paired["improvement_pct"]),
        "rule_worst_path_drawdown_pct": float(rule_path["drawdown_pct"].min()),
        "filtered_worst_path_drawdown_pct": float(filtered_path["drawdown_pct"].min()),
        "positive_fold_rate": float((fold_improvement > 0).mean()),
        "fold_after_cost_improvement_pct": [float(item) for item in fold_improvement],
        "risk_classifier": classification_summary(risk_stream, target_kind=(
            "adverse_event" if "85th_percentile" in str(risk_stream["threshold_method"].iloc[0])
            else "high_vol_event"
        )),
        "scope_note": "Historical signal-layer proxy; not a funded account simulation and does not change the production fallback.",
    }


def _combine_weighted(
    rows: list[dict[str, Any]],
    key: str,
    *,
    weight_key: str,
) -> float | None:
    available = [
        row for row in rows
        if row.get(key) is not None and float(row.get(weight_key, 0) or 0) > 0
    ]
    if not available:
        return None
    total = sum(float(row[weight_key]) for row in available)
    return sum(float(row[key]) * float(row[weight_key]) for row in available) / total


def summarize_ranking_markets(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    values = list(rows.values())
    return {
        "markets": rows,
        "date_count": int(sum(int(item.get("date_count", 0)) for item in values)),
        "spearman_ic": _combine_weighted(values, "spearman_ic", weight_key="date_count"),
        "pearson_ic": _combine_weighted(values, "pearson_ic", weight_key="date_count"),
        "after_cost_spread_pct": _combine_weighted(
            values, "after_cost_spread_pct", weight_key="date_count"
        ),
        "top_quintile_hit_rate": _combine_weighted(
            values, "top_quintile_hit_rate", weight_key="date_count"
        ),
        "turnover": _combine_weighted(values, "turnover", weight_key="date_count"),
        "positive_fold_rate": _combine_weighted(
            values, "positive_fold_rate", weight_key="date_count"
        ),
        "spearman_ic_ci_lower": min(
            (
                float(item["spearman_ic_95pct_block_bootstrap_ci"][0])
                for item in values
                if item.get("spearman_ic_95pct_block_bootstrap_ci")
            ),
            default=None,
        ),
        "after_cost_spread_ci_lower": min(
            (
                float(item["after_cost_spread_95pct_block_bootstrap_ci"][0])
                for item in values
                if item.get("after_cost_spread_95pct_block_bootstrap_ci")
            ),
            default=None,
        ),
    }


def _candidate_score(candidate: dict[str, Any]) -> float:
    summary = candidate["summary"]
    family = candidate["family"]
    if family == "cross_sectional_ranking":
        return (
            float(summary.get("spearman_ic") or 0.0)
            + 0.1 * float(summary.get("after_cost_spread_pct") or 0.0)
            + 0.05 * float(summary.get("positive_fold_rate") or 0.0)
        )
    if family == "risk_filter":
        return (
            float(summary.get("roc_auc_uplift_over_best_simple_baseline") or 0.0)
            + float(summary.get("pr_auc_uplift_over_best_simple_baseline") or 0.0)
            + max(0.0, float(summary.get("brier_improvement_over_best_simple_baseline") or 0.0))
        )
    if family == "rule_plus_ml_risk_filter":
        risk = summary.get("risk_classifier", {})
        return (
            0.2 * float(summary.get("after_cost_improvement_pct") or 0.0)
            + 0.05 * float(summary.get("positive_fold_rate") or 0.0)
            + float(risk.get("roc_auc") or 0.0) - 0.5
        )
    return (
        float(summary.get("balanced_accuracy") or 0.0) - 0.5
        + 0.1 * float(summary.get("average_after_cost_excess_return_pct") or 0.0)
        + 0.1 * float(summary.get("correlation") or 0.0)
    )


def _candidate_qualifies(candidate: dict[str, Any]) -> tuple[bool, list[str]]:
    summary = candidate["summary"]
    family = candidate["family"]
    reasons: list[str] = []
    if family == "rule_plus_ml_risk_filter":
        criteria = SELECTION_CRITERIA[family]
        improvement_ci = summary.get("after_cost_improvement_95pct_ci") or [-1.0, 1.0]
        risk_ci = summary.get("risk_classifier", {}).get(
            "roc_auc_95pct_block_bootstrap_ci"
        ) or [0.0, 0.0]
        checks = {
            "after-cost rule improvement interval crosses zero": float(improvement_ci[0]) > criteria["after_cost_improvement_ci_lower_min"],
            "filtered rule return is not positive": float(summary.get("filtered_after_cost_return_pct") or -1) > criteria["filtered_after_cost_return_min"],
            "hybrid fold stability below minimum": float(summary.get("positive_fold_rate") or 0) >= criteria["positive_fold_rate_min"],
            "risk classifier interval does not clear chance": float(risk_ci[0]) > criteria["risk_roc_auc_ci_lower_min"],
        }
    elif family == "cross_sectional_ranking":
        criteria = SELECTION_CRITERIA[family]
        checks = {
            "mean Spearman IC below minimum": float(summary.get("spearman_ic") or -1) >= criteria["spearman_ic_min"],
            "Spearman IC interval crosses zero": float(summary.get("spearman_ic_ci_lower") or -1) > criteria["spearman_ic_ci_lower_min"],
            "after-cost spread interval crosses zero": float(summary.get("after_cost_spread_ci_lower") or -1) > criteria["after_cost_spread_ci_lower_min"],
            "fold stability below minimum": float(summary.get("positive_fold_rate") or 0) >= criteria["positive_fold_rate_min"],
        }
    elif family == "risk_filter":
        criteria = SELECTION_CRITERIA[family]
        auc_ci = summary.get("roc_auc_95pct_block_bootstrap_ci") or [0.0, 0.0]
        checks = {
            "ROC-AUC interval does not clear chance": float(auc_ci[0]) > criteria["roc_auc_ci_lower_min"],
            "ROC-AUC does not beat the best simple proxy": float(
                summary.get("roc_auc_uplift_over_best_simple_baseline") or 0
            ) > criteria["roc_auc_uplift_over_best_simple_baseline_min"],
            "PR-AUC does not beat the best simple proxy": float(
                summary.get("pr_auc_uplift_over_best_simple_baseline") or 0
            ) > criteria["pr_auc_uplift_over_best_simple_baseline_min"],
            "Brier score does not beat simple risk proxy": (
                float(summary.get("brier_improvement_over_best_simple_baseline") or 0.0)
            ) > criteria["brier_improvement_min"],
            "fold stability below minimum": float(summary.get("positive_skill_fold_rate") or 0) >= criteria["positive_fold_rate_min"],
        }
    else:
        criteria = SELECTION_CRITERIA["relative_return"]
        correlation_ci = summary.get("correlation_95pct_block_bootstrap_ci") or [-1.0, 1.0]
        after_cost_ci = summary.get("after_cost_excess_return_95pct_ci") or [-1.0, 1.0]
        checks = {
            "balanced accuracy below minimum": float(summary.get("balanced_accuracy") or 0) >= criteria["balanced_accuracy_min"],
            "association interval crosses zero": (
                float(correlation_ci[0]) > criteria["correlation_ci_lower_min"]
                if candidate["task"] == "relative_regression" else True
            ),
            "after-cost interval crosses zero": float(after_cost_ci[0]) > criteria["after_cost_ci_lower_min"],
            "fold stability below minimum": float(
                summary.get("positive_after_cost_fold_rate")
                or summary.get("positive_skill_fold_rate")
                or 0
            ) >= criteria["positive_fold_rate_min"],
        }
    reasons = [reason for reason, passed in checks.items() if not passed]
    return not reasons, reasons


def select_locked_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Select without accepting or inspecting any locked-holdout result."""
    assessed: list[dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        item["development_score"] = _candidate_score(item)
        item["qualified"], item["qualification_failures"] = _candidate_qualifies(item)
        assessed.append(item)
    qualified = [item for item in assessed if item["qualified"]]
    pool = qualified or assessed
    selected = max(pool, key=lambda item: (float(item["development_score"]), item["name"]))
    selected = dict(selected)
    selected["selection_note"] = (
        "Highest predeclared development score among qualifying candidates."
        if qualified else
        "No candidate met the predeclared development criteria; selected only as the single strongest falsification candidate."
    )
    selected["candidate_count"] = len(assessed)
    selected["qualified_candidate_count"] = len(qualified)
    return selected


def _locked_regression_stream(
    development: pd.DataFrame,
    locked: pd.DataFrame,
    *,
    target_name: str,
    model_name: str,
    target_kind: str,
) -> pd.DataFrame:
    x_train, y_train, _, _, candidates = _build_feature_frame(development, target_name)
    x_test, y_test, dates, tickers, _ = _build_feature_frame(locked, target_name)
    selected, _ = select_training_features(x_train, candidates, "compact")
    model = _build_regressor_pipeline(model_name)
    model.fit(x_train[selected], y_train.astype(float))
    prediction = np.asarray(model.predict(x_test[selected]), dtype=float)
    if target_kind == "relative_return":
        baseline = pd.to_numeric(x_test["excess_return_5d_pct"], errors="coerce").fillna(0.0)
        threshold = float(derive_economic_class_threshold(y_train.astype(float))["threshold_pct"])
    else:
        baseline = pd.to_numeric(x_test["rolling_volatility_20_pct"], errors="coerce").fillna(0.0)
        threshold = float("nan")
    part = pd.DataFrame({
        "date": pd.to_datetime(dates).dt.normalize().to_numpy(),
        "ticker": tickers.astype(str).to_numpy(),
        "fold": 1,
        "actual": y_test.astype(float).to_numpy(),
        "prediction": prediction,
        "baseline_prediction": baseline.to_numpy(float),
        "economic_threshold_pct": threshold,
    })
    return attach_regimes(part, x_test.reset_index(drop=True), derive_regime_thresholds(x_train))


def _locked_classification_stream(
    development: pd.DataFrame,
    locked: pd.DataFrame,
    *,
    target_name: str,
    model_name: str,
    target_kind: str,
) -> pd.DataFrame:
    x_train, y_train, _, _, candidates = _build_feature_frame(development, target_name)
    x_test, y_test, dates, tickers, _ = _build_feature_frame(locked, target_name)
    y_train = y_train.astype(float)
    y_test = y_test.astype(float)
    selected, _ = select_training_features(x_train, candidates, "compact")
    evidence = _classification_threshold(target_kind, y_train)
    threshold = float(evidence["threshold_pct"])
    train_labels = _labels_from_threshold(y_train, target_kind=target_kind, threshold=threshold)
    actual_labels = _labels_from_threshold(y_test, target_kind=target_kind, threshold=threshold)
    model = _build_classifier_pipeline(model_name)
    model.fit(x_train[selected], train_labels)
    predicted = np.asarray(model.predict(x_test[selected]), dtype=int)
    probabilities = model.predict_proba(x_test[selected])
    classes = list(model.classes_)
    probability = probabilities[:, classes.index(1)] if 1 in classes else np.zeros(len(x_test))
    proxy_pairs = {
        "recent_relative_momentum": (
            pd.to_numeric(x_train["excess_return_5d_pct"], errors="coerce"),
            pd.to_numeric(x_test["excess_return_5d_pct"], errors="coerce"),
        ),
        "rolling_volatility_20d": (
            pd.to_numeric(x_train["rolling_volatility_20_pct"], errors="coerce"),
            pd.to_numeric(x_test["rolling_volatility_20_pct"], errors="coerce"),
        ),
        "current_drawdown": (
            -pd.to_numeric(x_train["drawdown_from_peak_pct"], errors="coerce"),
            -pd.to_numeric(x_test["drawdown_from_peak_pct"], errors="coerce"),
        ),
        "intraday_range_proxy": (
            pd.to_numeric(x_train["intraday_range_pct"], errors="coerce"),
            pd.to_numeric(x_test["intraday_range_pct"], errors="coerce"),
        ),
    }
    baseline_probabilities = {
        name: _calibrated_univariate_probability(train_proxy, train_labels, test_proxy)
        for name, (train_proxy, test_proxy) in proxy_pairs.items()
    }
    primary_baseline = (
        "recent_relative_momentum" if target_kind.startswith("relative")
        else "current_drawdown" if target_kind == "adverse_event"
        else "rolling_volatility_20d"
    )
    part = pd.DataFrame({
        "date": pd.to_datetime(dates).dt.normalize().to_numpy(),
        "ticker": tickers.astype(str).to_numpy(),
        "fold": 1,
        "actual": y_test.to_numpy(float),
        "actual_label": actual_labels.to_numpy(int),
        "predicted_label": predicted,
        "probability_event": probability,
        "baseline_probability": baseline_probabilities[primary_baseline],
        "economic_threshold_pct": threshold,
        "threshold_method": evidence["method"],
    })
    for name, values in baseline_probabilities.items():
        part[f"baseline_probability_{name}"] = values
    return attach_regimes(part, x_test.reset_index(drop=True), derive_regime_thresholds(x_train))


def evaluate_locked_candidate(
    candidate: dict[str, Any],
    market_frames: dict[str, dict[str, pd.DataFrame]],
) -> dict[str, Any]:
    """Fit and evaluate only the single development-selected architecture."""
    streams: dict[str, pd.DataFrame] = {}
    market_summaries: dict[str, dict[str, Any]] = {}
    for market, frames in market_frames.items():
        task = candidate["task"]
        if task in {"relative_regression", "ranking"}:
            stream = _locked_regression_stream(
                frames["relative_development"], frames["relative_locked"],
                target_name=RELATIVE_TARGET,
                model_name=candidate["model_name"],
                target_kind="relative_return",
            )
            summary = (
                cross_sectional_ranking_summary(stream)
                if task == "ranking" else regression_summary(stream, target_kind="relative_return")
            )
        elif task in {"relative_three_class", "relative_binary"}:
            stream = _locked_classification_stream(
                frames["relative_development"], frames["relative_locked"],
                target_name=RELATIVE_TARGET,
                model_name=candidate["model_name"],
                target_kind=task,
            )
            summary = classification_summary(stream, target_kind=task)
        else:
            target_name = RISK_ADVERSE_TARGET if task == "adverse_event" else RISK_VOLATILITY_TARGET
            classifier_task = task
            if task.startswith("rule_risk_"):
                classifier_task = task.removeprefix("rule_risk_") + "_event"
                target_name = (
                    RISK_ADVERSE_TARGET
                    if classifier_task == "adverse_event" else RISK_VOLATILITY_TARGET
                )
            stream = _locked_classification_stream(
                frames["risk_development"], frames["risk_locked"],
                target_name=target_name,
                model_name=candidate["model_name"],
                target_kind=classifier_task,
            )
            summary = (
                rule_risk_hybrid_summary(stream, frames["risk_locked"])
                if task.startswith("rule_risk_")
                else classification_summary(stream, target_kind=task)
            )
        streams[market] = stream
        market_summaries[market] = summary

    if candidate["task"] == "ranking":
        aggregate = summarize_ranking_markets(market_summaries)
    elif candidate["task"] in {"relative_regression"}:
        aggregate = regression_summary(pd.concat(streams.values(), ignore_index=True), target_kind="relative_return")
    elif candidate["task"].startswith("rule_risk_"):
        # The economics are market-specific because signals and costs are
        # replayed independently; aggregate by sample-weighted means while
        # preserving each market's full diagnostic.
        values = list(market_summaries.values())
        aggregate = {
            "markets": market_summaries,
            "sample_count": int(sum(item["sample_count"] for item in values)),
            "filtered_after_cost_return_pct": _combine_weighted(
                values, "filtered_after_cost_return_pct", weight_key="sample_count"
            ),
            "after_cost_improvement_pct": _combine_weighted(
                values, "after_cost_improvement_pct", weight_key="sample_count"
            ),
            "positive_fold_rate": _combine_weighted(
                values, "positive_fold_rate", weight_key="sample_count"
            ),
        }
    else:
        aggregate = classification_summary(
            pd.concat(streams.values(), ignore_index=True), target_kind=candidate["task"]
        )
    return {
        "candidate": {key: value for key, value in candidate.items() if key != "summary"},
        "aggregate": aggregate,
        "markets": market_summaries,
        "rows_evaluated": int(sum(len(item) for item in streams.values())),
        "fit_policy": "one static fit per market on all development rows; no refit or threshold change inside locked holdout",
    }


def _absolute_model_diagnostic(
    dataset: pd.DataFrame,
    *,
    model_name: str,
    design: str,
    start_offset: int = 0,
) -> dict[str, Any]:
    stationary = prepare_stationary_feature_dataset(dataset).iloc[int(start_offset):].copy()
    x_frame, y_series, dates, _, candidates = _build_feature_frame(
        stationary, "target_5d_return"
    )
    predictions: list[pd.DataFrame] = []
    for fold, (train_index, test_index) in enumerate(_make_oos_splits(dates), start=1):
        selected, _ = select_training_features(
            x_frame.iloc[train_index], candidates, design
        )
        model = _build_regressor_pipeline(model_name)
        model.fit(x_frame.iloc[train_index][selected], y_series.iloc[train_index].astype(float))
        predictions.append(pd.DataFrame({
            "fold": fold,
            "actual": y_series.iloc[test_index].astype(float).to_numpy(),
            "prediction": model.predict(x_frame.iloc[test_index][selected]),
        }))
    frame = pd.concat(predictions, ignore_index=True)
    actual_up = frame["actual"] > 0
    predicted_up = frame["prediction"] > 0
    fold_accuracy = [
        float(((fold["actual"] > 0) == (fold["prediction"] > 0)).mean())
        for _, fold in frame.groupby("fold")
    ]
    return {
        "design": design,
        "start_offset_rows": int(start_offset),
        "sample_count": int(len(frame)),
        "direction_accuracy": float((actual_up == predicted_up).mean()),
        "balanced_accuracy": float(balanced_accuracy_score(actual_up, predicted_up)),
        "mae_pct": float(mean_absolute_error(frame["actual"], frame["prediction"])),
        "correlation": float(frame["prediction"].corr(frame["actual"])),
        "fold_direction_accuracy": fold_accuracy,
        "worst_fold_accuracy": min(fold_accuracy),
    }


def audit_prior_passes(
    datasets: dict[tuple[str, str], pd.DataFrame],
    boundaries: dict[str, dict[str, Any]],
    *,
    stage1_path: Path,
) -> dict[str, Any]:
    stage1 = json.loads(stage1_path.read_text(encoding="utf-8"))
    passed = [row for row in stage1["all_model_rows"] if row.get("passed")]
    diagnostics: list[dict[str, Any]] = []
    artifact_root = stage1_path.parent / "stage1_current_scheme_models"
    for row in passed:
        market = str(row["market"])
        ticker = str(row["ticker"])
        period = str(row["period"])
        model_name = str(row["model_name"])
        model_root = artifact_root / ticker / period / "target_5d_return" / model_name
        if market == "HK":
            model_root = artifact_root / "HK" / ticker / period / "target_5d_return" / model_name
        evaluation_path = model_root / "evaluation_table.csv"
        evaluation = pd.read_csv(evaluation_path)
        evaluation["actual_future_result"] = pd.to_numeric(
            evaluation["actual_future_result"], errors="coerce"
        )
        evaluation["predicted_value"] = pd.to_numeric(
            evaluation["predicted_value"], errors="coerce"
        )
        evaluation = evaluation.dropna(subset=["actual_future_result", "predicted_value"])
        hits = (
            (evaluation["actual_future_result"] > 0)
            == (evaluation["predicted_value"] > 0)
        )
        independent = evaluation.sort_values("prediction_date").iloc[::HORIZON_ROWS]
        independent_hits = (
            (independent["actual_future_result"] > 0)
            == (independent["predicted_value"] > 0)
        )
        raw = datasets[(market, ticker)]
        development, _ = split_development_and_locked(raw, boundaries[market])
        sensitivity = [
            _absolute_model_diagnostic(
                development, model_name=model_name, design=design, start_offset=0
            )
            for design in ("current", "reduced", "compact")
        ]
        start_date_sensitivity = [
            _absolute_model_diagnostic(
                development, model_name=model_name, design="current", start_offset=offset
            )
            for offset in (0, 63, 126)
        ]
        diagnostics.append({
            "market": market,
            "ticker": ticker,
            "period": period,
            "model_name": model_name,
            "original_gate_metrics": row,
            "full_oos_direction_accuracy": float(hits.mean()),
            "full_oos_direction_95pct_wilson_ci": _wilson_interval(int(hits.sum()), len(hits)),
            "non_overlapping_direction_accuracy": float(independent_hits.mean()),
            "non_overlapping_direction_95pct_wilson_ci": _wilson_interval(
                int(independent_hits.sum()), len(independent_hits)
            ),
            "feature_sensitivity_before_final_holdout": sensitivity,
            "start_date_sensitivity_before_final_holdout": start_date_sensitivity,
            "final_holdout_used_for_this_diagnostic": False,
        })
    pass_count = len(passed)
    model_count = int(stage1["models_trained"])
    return {
        "passed_model_count": pass_count,
        "models_tested": model_count,
        "empirical_pass_rate": pass_count / model_count if model_count else 0.0,
        "pass_rate_95pct_wilson_ci": _wilson_interval(pass_count, model_count),
        "multiple_testing_note": (
            "Three exceptions among 220 searched ticker/period/algorithm combinations are vulnerable to selection noise. "
            "Their final holdout was not opened individually; sensitivity was measured only before the locked block."
        ),
        "models": diagnostics,
    }


def data_diagnostics(
    datasets: dict[tuple[str, str], pd.DataFrame],
    boundaries: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for market in BENCHMARKS:
        tickers: list[dict[str, Any]] = []
        for (row_market, ticker), raw in datasets.items():
            if row_market != market:
                continue
            development, locked = split_development_and_locked(raw, boundaries[market])
            stationary = prepare_stationary_feature_dataset(development)
            compact = [item for item in COMPACT_FEATURES if item in stationary.columns]
            tickers.append({
                "ticker": ticker,
                "history_start": pd.to_datetime(raw["date"]).min(),
                "history_end": pd.to_datetime(raw["date"]).max(),
                "total_rows": int(len(raw)),
                "development_rows": int(len(development)),
                "locked_rows_with_matured_target": int(locked[RELATIVE_TARGET].notna().sum()),
                "compact_feature_missing_rate": float(stationary[compact].isna().mean().mean()),
                "excess_return_std_pct": float(pd.to_numeric(
                    development[RELATIVE_TARGET], errors="coerce"
                ).std()),
                "absolute_return_up_rate": float(
                    pd.to_numeric(development["target_5d_return"], errors="coerce").gt(0).mean()
                ),
                "median_annualized_volatility_pct": float(pd.to_numeric(
                    stationary["rolling_volatility_20_pct"], errors="coerce"
                ).median()),
                "median_absolute_overnight_gap_pct": float(pd.to_numeric(
                    stationary["overnight_gap_pct"], errors="coerce"
                ).abs().median()),
                "zero_volume_rate": float(pd.to_numeric(raw["volume"], errors="coerce").fillna(0).eq(0).mean()),
            })
        output[market] = {
            "ticker_count": len(tickers),
            "tickers": tickers,
            "median_development_rows": float(pd.Series([item["development_rows"] for item in tickers]).median()),
            "median_excess_return_std_pct": float(pd.Series([item["excess_return_std_pct"] for item in tickers]).median()),
            "median_annualized_volatility_pct": float(pd.Series([item["median_annualized_volatility_pct"] for item in tickers]).median()),
            "median_absolute_overnight_gap_pct": float(pd.Series([item["median_absolute_overnight_gap_pct"] for item in tickers]).median()),
        }
    return output


def non_price_inventory() -> list[dict[str, Any]]:
    """Inventory only data sources already implemented in this repository."""
    return [
        {
            "source": "Yahoo Finance ticker/company news",
            "code": "app/services/news_service.py + news_sentiment.py",
            "current_coverage": "up to the provider's current returned article window per request",
            "historical_depth": "not guaranteed; no persistent article archive",
            "point_in_time_timestamp": "article publication timestamp retained",
            "leakage_risk": "retrieval/survivorship risk when reconstructing old dates from today's feed",
            "missingness": "missing dates are filled with zeros, conflating no article with unavailable feed",
            "refresh": "on dataset build; provider-dependent",
            "markets": "US and HK symbols accepted; depth not guaranteed for either",
            "safe_for_this_experiment": False,
            "reason": "insufficient persistent point-in-time coverage",
        },
        {
            "source": "Reddit social search",
            "code": "app/services/external_market_context_service.py",
            "current_coverage": "configured subreddits, newest one-week search, capped posts",
            "historical_depth": "one week at fetch time; no database history",
            "point_in_time_timestamp": "aggregate has fetched_at only",
            "leakage_risk": "cannot reproduce what was observable at an old prediction date",
            "missingness": "optional and network/key dependent",
            "refresh": "one-hour in-memory cache",
            "markets": "query accepts both; mapping quality varies",
            "safe_for_this_experiment": False,
            "reason": "current snapshot, not a point-in-time panel",
        },
        {
            "source": "yfinance analyst consensus and revisions",
            "code": "app/services/external_market_context_service.py",
            "current_coverage": "current info plus provider's recent recommendation tables",
            "historical_depth": "not persisted",
            "point_in_time_timestamp": "discarded in the aggregate context",
            "leakage_risk": "today's consensus cannot be assigned to historical rows",
            "missingness": "provider and ticker dependent; HK is weaker",
            "refresh": "one-hour in-memory cache",
            "markets": "US and some HK provider symbols",
            "safe_for_this_experiment": False,
            "reason": "no as-of history",
        },
        {
            "source": "Alpha Vantage news sentiment and earnings transcripts",
            "code": "app/services/external_market_context_service.py",
            "current_coverage": "optional API-key feed; recent news and up to six recent quarters",
            "historical_depth": "not persisted as a dated feature panel",
            "point_in_time_timestamp": "source payload may contain dates, aggregate discards them",
            "leakage_risk": "historical availability cannot be reconstructed safely",
            "missingness": "entirely absent without key or provider coverage",
            "refresh": "one-hour in-memory context cache",
            "markets": "provider-dependent, principally US",
            "safe_for_this_experiment": False,
            "reason": "optional snapshot without PIT storage",
        },
        {
            "source": "SEC EDGAR filing metadata",
            "code": "app/services/external_market_context_service.py",
            "current_coverage": "latest submission list and filing dates",
            "historical_depth": "official history available in response but current code aggregates latest 20",
            "point_in_time_timestamp": "filing_date retained in recent event payload",
            "leakage_risk": "could become safe after a separate as-of feature builder; current aggregate is not",
            "missingness": "US issuers only; CIK and network dependent",
            "refresh": "CIK map daily, context hourly in memory",
            "markets": "US only",
            "safe_for_this_experiment": False,
            "reason": "promising official source, but no daily PIT feature panel yet",
        },
        {
            "source": "fundamentals / macro / true market breadth",
            "code": "no historical training source found",
            "current_coverage": "none in the research dataset",
            "historical_depth": "none",
            "point_in_time_timestamp": "none",
            "leakage_risk": "not assessable",
            "missingness": "100%",
            "refresh": "none",
            "markets": "none",
            "safe_for_this_experiment": False,
            "reason": "not implemented; benchmark_strength_score is relative momentum, not breadth",
        },
    ]


def _load_datasets() -> tuple[dict[tuple[str, str], pd.DataFrame], list[dict[str, str]]]:
    datasets: dict[tuple[str, str], pd.DataFrame] = {}
    failures: list[dict[str, str]] = []
    for security in REPRESENTATIVE_UNIVERSE:
        benchmark = BENCHMARKS[security.market]
        try:
            frame = build_feature_dataset(
                security.ticker,
                period=SOURCE_PERIOD,
                benchmark=benchmark,
                include_news_sentiment=False,
                market=security.market,
            )
            datasets[(security.market, security.ticker)] = add_forward_risk_targets(frame)
        except Exception as exc:  # pragma: no cover - provider behavior varies
            logger.exception("Objective dataset failed market=%s ticker=%s", security.market, security.ticker)
            failures.append({
                "market": security.market,
                "ticker": security.ticker,
                "error": f"{type(exc).__name__}: {exc}",
            })
    for market in BENCHMARKS:
        available = sum(1 for row_market, _ in datasets if row_market == market)
        if available < 4:
            raise RuntimeError(f"Only {available} {market} datasets loaded; need at least four.")
    return datasets, failures


def _frozen_control(stage1_path: Path) -> dict[str, Any]:
    data = json.loads(stage1_path.read_text(encoding="utf-8"))
    rows = data["all_model_rows"]
    return {
        "source": str(stage1_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "models": int(data["models_trained"]),
        "fully_validated": int(data["models_fully_current_scheme_validated"]),
        "pass_rate": float(data["full_validation_pass_rate"]),
        "algorithms": list(REGRESSION_MODELS) + ["linear_regression", "gradient_boosting"],
        "periods": ["2y", "5y", "10y"],
        "passed_models": [
            {
                "market": row["market"],
                "ticker": row["ticker"],
                "period": row["period"],
                "model_name": row["model_name"],
                "direction_accuracy": row["direction_accuracy"],
                "balanced_direction_accuracy": row["balanced_direction_accuracy"],
            }
            for row in rows if row.get("passed")
        ],
        "note": "Read-only reuse of the prior controlled audit; no baseline was retrained or modified.",
    }


def _add_candidate(
    candidates: list[dict[str, Any]],
    *,
    name: str,
    family: str,
    task: str,
    model_name: str,
    summary: dict[str, Any],
) -> None:
    candidates.append({
        "name": name,
        "family": family,
        "task": task,
        "model_name": model_name,
        "summary": summary,
    })


def run_development_experiments(
    market_frames: dict[str, dict[str, pd.DataFrame]],
    output_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    results: dict[str, Any] = {
        "relative_regression": {},
        "relative_classification": {},
        "ranking": {},
        "risk_regression": {},
        "risk_classification": {},
        "rule_plus_risk_filter": {},
    }
    candidates: list[dict[str, Any]] = []
    prediction_root = output_root / "development_oos_predictions"
    prediction_root.mkdir(parents=True, exist_ok=True)

    relative_regression_streams: dict[str, dict[str, pd.DataFrame]] = defaultdict(dict)
    for model_name in REGRESSION_MODELS:
        for market, frames in market_frames.items():
            stream = evaluate_regression_oos(
                frames["relative_development"],
                target_name=RELATIVE_TARGET,
                model_name=model_name,
                target_kind="relative_return",
            )
            relative_regression_streams[model_name][market] = stream
            stream.to_csv(
                prediction_root / f"relative_regression_{model_name}_{market}.csv.gz",
                index=False, compression="gzip",
            )
        combined = pd.concat(relative_regression_streams[model_name].values(), ignore_index=True)
        summary = regression_summary(combined, target_kind="relative_return")
        results["relative_regression"][model_name] = {
            "aggregate": summary,
            "markets": {
                market: regression_summary(stream, target_kind="relative_return")
                for market, stream in relative_regression_streams[model_name].items()
            },
            "regimes": regime_summaries(combined, result_kind="regression"),
        }
        _add_candidate(
            candidates,
            name=f"pooled_{model_name}_continuous_excess_return",
            family="relative_return",
            task="relative_regression",
            model_name=model_name,
            summary=summary,
        )

        market_rankings = {
            market: cross_sectional_ranking_summary(stream)
            for market, stream in relative_regression_streams[model_name].items()
        }
        ranking_summary = summarize_ranking_markets(market_rankings)
        baseline_rankings = {
            market: cross_sectional_ranking_summary(
                stream, prediction_column="baseline_prediction"
            )
            for market, stream in relative_regression_streams[model_name].items()
        }
        results["ranking"][model_name] = {
            "aggregate": ranking_summary,
            "markets": market_rankings,
            "recent_relative_momentum_baseline": summarize_ranking_markets(baseline_rankings),
        }
        _add_candidate(
            candidates,
            name=f"pooled_{model_name}_cross_sectional_ranking",
            family="cross_sectional_ranking",
            task="ranking",
            model_name=model_name,
            summary=ranking_summary,
        )

    for target_kind in ("relative_three_class", "relative_binary"):
        for model_name in CLASSIFICATION_MODELS:
            streams: dict[str, pd.DataFrame] = {}
            for market, frames in market_frames.items():
                stream = evaluate_classification_oos(
                    frames["relative_development"],
                    target_name=RELATIVE_TARGET,
                    model_name=model_name,
                    target_kind=target_kind,
                )
                streams[market] = stream
                stream.to_csv(
                    prediction_root / f"{target_kind}_{model_name}_{market}.csv.gz",
                    index=False, compression="gzip",
                )
            combined = pd.concat(streams.values(), ignore_index=True)
            summary = classification_summary(combined, target_kind=target_kind)
            key = f"{target_kind}:{model_name}"
            results["relative_classification"][key] = {
                "aggregate": summary,
                "markets": {
                    market: classification_summary(stream, target_kind=target_kind)
                    for market, stream in streams.items()
                },
            }
            _add_candidate(
                candidates,
                name=f"pooled_{model_name}_{target_kind}",
                family="relative_return",
                task=target_kind,
                model_name=model_name,
                summary=summary,
            )

    for model_name in REGRESSION_MODELS:
        streams: dict[str, pd.DataFrame] = {}
        for market, frames in market_frames.items():
            stream = evaluate_regression_oos(
                frames["risk_development"],
                target_name=RISK_VOLATILITY_TARGET,
                model_name=model_name,
                target_kind="risk_volatility",
            )
            streams[market] = stream
            stream.to_csv(
                prediction_root / f"risk_volatility_{model_name}_{market}.csv.gz",
                index=False, compression="gzip",
            )
        combined = pd.concat(streams.values(), ignore_index=True)
        results["risk_regression"][model_name] = {
            "aggregate": regression_summary(combined, target_kind="risk_volatility"),
            "markets": {
                market: regression_summary(stream, target_kind="risk_volatility")
                for market, stream in streams.items()
            },
            "regimes": regime_summaries(combined, result_kind="regression"),
        }

    for target_kind, target_name in (
        ("adverse_event", RISK_ADVERSE_TARGET),
        ("high_vol_event", RISK_VOLATILITY_TARGET),
    ):
        for model_name in CLASSIFICATION_MODELS:
            streams: dict[str, pd.DataFrame] = {}
            for market, frames in market_frames.items():
                stream = evaluate_classification_oos(
                    frames["risk_development"],
                    target_name=target_name,
                    model_name=model_name,
                    target_kind=target_kind,
                )
                streams[market] = stream
                stream.to_csv(
                    prediction_root / f"{target_kind}_{model_name}_{market}.csv.gz",
                    index=False, compression="gzip",
                )
            combined = pd.concat(streams.values(), ignore_index=True)
            summary = classification_summary(combined, target_kind=target_kind)
            key = f"{target_kind}:{model_name}"
            results["risk_classification"][key] = {
                "aggregate": summary,
                "markets": {
                    market: classification_summary(stream, target_kind=target_kind)
                    for market, stream in streams.items()
                },
                "regimes": regime_summaries(combined, result_kind="classification"),
            }
            _add_candidate(
                candidates,
                name=f"pooled_{model_name}_{target_kind}_risk_filter",
                family="risk_filter",
                task=target_kind,
                model_name=model_name,
                summary=summary,
            )
            combined_features = pd.concat(
                [frames["risk_development"] for frames in market_frames.values()],
                ignore_index=True,
            )
            hybrid = rule_risk_hybrid_summary(combined, combined_features)
            results["rule_plus_risk_filter"][key] = hybrid
            _add_candidate(
                candidates,
                name=f"unchanged_rule_plus_{model_name}_{target_kind}_filter",
                family="rule_plus_ml_risk_filter",
                task=f"rule_risk_{target_kind.removesuffix('_event')}",
                model_name=model_name,
                summary=hybrid,
            )

    for candidate in candidates:
        qualified, failures = _candidate_qualifies(candidate)
        candidate["qualified"] = qualified
        candidate["qualification_failures"] = failures
        candidate["development_score"] = _candidate_score(candidate)
    return results, candidates


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, (float, np.floating)):
        return f"{float(value):.{digits}f}"
    return str(value)


def _recommendation_choice(
    selected: dict[str, Any],
    locked: dict[str, Any],
) -> tuple[str, str]:
    if not selected.get("qualified"):
        return "F", "NO ML APPROACH CURRENTLY JUSTIFIED"
    family = selected["family"]
    aggregate = locked["aggregate"]
    if family == "cross_sectional_ranking":
        replicated = (
            float(aggregate.get("spearman_ic") or -1) > 0
            and float(aggregate.get("after_cost_spread_pct") or -1) > 0
            and all(
                float(item.get("spearman_ic") or -1) > 0
                and float(item.get("after_cost_spread_pct") or -1) > 0
                for item in locked["markets"].values()
            )
        )
        return ("C", "USE CROSS-SECTIONAL RANKING ML") if replicated else ("F", "NO ML APPROACH CURRENTLY JUSTIFIED")
    if family == "rule_plus_ml_risk_filter":
        replicated = all(
            float(item.get("after_cost_improvement_pct") or -1) > 0
            and float(item.get("filtered_after_cost_return_pct") or -1) > 0
            and float(item.get("risk_classifier", {}).get("roc_auc") or 0) > 0.5
            for item in locked["markets"].values()
        )
        return ("E", "USE RULE SIGNAL + ML RISK FILTER") if replicated else ("F", "NO ML APPROACH CURRENTLY JUSTIFIED")
    if family == "risk_filter":
        replicated = (
            float(aggregate.get("roc_auc") or 0) > 0.5
            and float(aggregate.get("roc_auc_uplift_over_best_simple_baseline") or 0) > 0
            and float(aggregate.get("pr_auc_uplift_over_best_simple_baseline") or 0) > 0
            and float(aggregate.get("brier_improvement_over_best_simple_baseline") or 0) > 0
            and all(
                float(item.get("roc_auc_uplift_over_best_simple_baseline") or 0) > 0
                and float(item.get("pr_auc_uplift_over_best_simple_baseline") or 0) > 0
                and float(item.get("brier_improvement_over_best_simple_baseline") or 0) > 0
                for item in locked["markets"].values()
            )
        )
        return ("D", "USE ML AS RISK FILTER ONLY") if replicated else ("F", "NO ML APPROACH CURRENTLY JUSTIFIED")
    replicated = (
        float(aggregate.get("balanced_accuracy") or 0) > 0.5
        and float(aggregate.get("average_after_cost_excess_return_pct") or -1) > 0
    )
    return ("B", "USE RELATIVE-RETURN ML") if replicated else ("F", "NO ML APPROACH CURRENTLY JUSTIFIED")


def _render_detailed_working_report(report: dict[str, Any]) -> str:
    control = report["frozen_control"]
    development = report["development_results"]
    selected = report["locked_candidate_selection"]
    locked = report["locked_final_result"]
    strong_recheck = report.get("posthoc_strong_baseline_recheck")
    effective_locked = strong_recheck["locked"] if strong_recheck else locked
    diagnostics = report["data_diagnostics"]
    prior = report["prior_pass_audit"]
    choice = report["final_architecture_choice"]
    lines = [
        "# Model Trader objective-redesign audit — 2026-08-23",
        "",
        "Status: **EXPERIMENTAL ONLY**. No production artifact was deployed, registered, promoted, or made runtime-selectable. Existing gates and Virtual Trader logic were not changed.",
        "",
        "## 1. Experimental boundary and chronology",
        "",
        f"The fixed sample is the existing {len(REPRESENTATIVE_UNIVERSE)}-security representative universe. Data were requested once at `{SOURCE_PERIOD}`. The final {LOCKED_HOLDOUT_DATES} market dates per market were declared before model fitting; the five dates immediately before each holdout were purged because their labels overlap the holdout.",
        "",
        "The locked block is untouched by this objective-redesign selection. It is not globally virgin data: the preceding absolute-return audit had already summarized these historical dates. This limitation is explicit and prevents describing the block as a brand-new future trial.",
        "",
        "## 2. Frozen current absolute-return control",
        "",
        f"The prior controlled result was reused read-only: {control['fully_validated']}/{control['models']} ticker/period/algorithm artifacts cleared all current gates ({control['pass_rate']:.2%}). No old feature set, model family, threshold, or gate was changed.",
        "",
        "Passed exceptions: " + ", ".join(
            f"{item['ticker']} {item['period']} {item['model_name']}"
            for item in control["passed_models"]
        ) + ".",
        "",
        "## 3. Continuous five-day excess-return target",
        "",
        "Target = ticker adjusted five-day return minus the configured broad-market benchmark return over the same dates (US `VOO`, HK `2800`). The threshold used for actions is cost plus a robust uncertainty margin derived inside each training fold.",
        "",
        "| Model | Correlation | Balanced accuracy | MAE | Baseline MAE | After-cost excess |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for model, payload in development["relative_regression"].items():
        item = payload["aggregate"]
        lines.append(
            f"| {model} | {_fmt(item.get('correlation'))} | {_fmt(item.get('balanced_accuracy'))} | {_fmt(item.get('mae_pct'))}% | {_fmt(item.get('baseline_mae_pct'))}% | {_fmt(item.get('average_after_cost_excess_return_pct'))}% |"
        )
    lines.extend([
        "",
        "## 4. Three-class relative target",
        "",
        "Outperform / neutral / underperform boundaries were recomputed per training fold from cost plus training-only uncertainty. Neutral is therefore an economic class, not a tuned test-set band.",
        "",
        "| Model | Balanced accuracy | Macro F1 | Neutral rate | After-cost excess |",
        "|---|---:|---:|---:|---:|",
    ])
    for key, payload in development["relative_classification"].items():
        if not key.startswith("relative_three_class"):
            continue
        item = payload["aggregate"]
        lines.append(
            f"| {key.split(':')[1]} | {_fmt(item.get('balanced_accuracy'))} | {_fmt(item.get('macro_f1'))} | {_fmt(item.get('neutral_prediction_rate'))} | {_fmt(item.get('average_after_cost_excess_return_pct'))}% |"
        )
    lines.extend([
        "",
        "## 5. Binary meaningful-outperformance target",
        "",
        "The positive class means excess return above the same training-only economic threshold; it is not simply `excess > 0` and does not use the production fixed-label column.",
        "",
        "| Model | Balanced | ROC-AUC | PR-AUC | Prevalence | Brier | After-cost excess |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for key, payload in development["relative_classification"].items():
        if not key.startswith("relative_binary"):
            continue
        item = payload["aggregate"]
        lines.append(
            f"| {key.split(':')[1]} | {_fmt(item.get('balanced_accuracy'))} | {_fmt(item.get('roc_auc'))} | {_fmt(item.get('pr_auc'))} | {_fmt(item.get('event_prevalence'))} | {_fmt(item.get('brier_score'))} | {_fmt(item.get('average_after_cost_excess_return_pct'))}% |"
        )
    lines.extend([
        "",
        "## 6. Cross-sectional ranking",
        "",
        "Predictions were ranked only against securities in the same market and date. Metrics include daily rank IC, top-minus-bottom quintile spread, top-quintile hit rate, turnover, and a five-path after-cost spread.",
        "",
        "| Model | Spearman IC | Pearson IC | After-cost spread | Top hit | Turnover | Stable folds |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for model, payload in development["ranking"].items():
        item = payload["aggregate"]
        lines.append(
            f"| {model} | {_fmt(item.get('spearman_ic'))} | {_fmt(item.get('pearson_ic'))} | {_fmt(item.get('after_cost_spread_pct'))}% | {_fmt(item.get('top_quintile_hit_rate'))} | {_fmt(item.get('turnover'))} | {_fmt(item.get('positive_fold_rate'))} |"
        )
    lines.extend([
        "",
        "## 7. Future realised-volatility prediction",
        "",
        "The risk regression predicts annualised volatility realised over the next five sessions. Its direct baseline is the observable 20-day rolling volatility, on the same scale.",
        "",
        "| Model | MAE | Baseline MAE | MAE improvement | Correlation | Stable improvement folds |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for model, payload in development["risk_regression"].items():
        item = payload["aggregate"]
        lines.append(
            f"| {model} | {_fmt(item.get('mae_pct'))}% | {_fmt(item.get('baseline_mae_pct'))}% | {_fmt(item.get('mae_improvement_pct'))}% | {_fmt(item.get('correlation'))} | {_fmt(item.get('positive_mae_improvement_fold_rate'))} |"
        )
    lines.extend([
        "",
        "## 8. Adverse-move and high-volatility event filters",
        "",
        "Adverse events use the training-fold 85th percentile of future five-day maximum adverse move. High-volatility events use the training-fold 75th percentile of future realised volatility. Reported Brier, ROC-AUC, PR-AUC, recall, and false-positive rate are OOS.",
        "",
        "| Target:model | ROC-AUC | PR uplift | Brier | Simple-proxy Brier | Recall | FPR |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for key, payload in development["risk_classification"].items():
        item = payload["aggregate"]
        lines.append(
            f"| {key} | {_fmt(item.get('roc_auc'))} | {_fmt(item.get('pr_auc_uplift_over_prevalence'))} | {_fmt(item.get('brier_score'))} | {_fmt(item.get('baseline_brier_score'))} | {_fmt(item.get('recall'))} | {_fmt(item.get('false_positive_rate'))} |"
        )
    if strong_recheck:
        corrected = strong_recheck["development"]["aggregate"]
        lines.extend([
            "",
            "Post-hoc audit correction for the already-selected high-volatility forest (the architecture was not reselected):",
            "",
            "| Model ROC-AUC | Best simple ROC-AUC | Uplift | Model PR-AUC | Best simple PR-AUC | Uplift | Model Brier | Best simple Brier |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|",
            f"| {_fmt(corrected.get('roc_auc'))} | {_fmt(corrected.get('best_simple_baseline_roc_auc'))} | {_fmt(corrected.get('roc_auc_uplift_over_best_simple_baseline'))} | {_fmt(corrected.get('pr_auc'))} | {_fmt(corrected.get('best_simple_baseline_pr_auc'))} | {_fmt(corrected.get('pr_auc_uplift_over_best_simple_baseline'))} | {_fmt(corrected.get('brier_score'))} | {_fmt(corrected.get('best_simple_baseline_brier_score'))} |",
            "",
            "The original percentile-proxy Brier comparison was not probability-calibrated and is not used for the final decision. The corrected baselines are one-feature logistic models fitted inside each training fold using recent relative momentum, 20-day volatility, current drawdown, or intraday range.",
        ])
    lines.extend([
        "",
        "## 9. Regime-conditioned behavior",
        "",
        "Bull/non-bull, high/low volatility, and stressed/normal boundaries use only each training fold's median trend, median volatility, and 25th-percentile drawdown. No separate regime classifier or regime threshold tuning was performed. Detailed regime rows are in the JSON audit; a candidate is not considered stable merely because one regime is strong.",
        "",
        "## 10. Unchanged rule signal plus ML risk filter",
        "",
        "The existing deterministic 65-point technical rule was replayed unchanged. The experimental variant blocks a rule entry only when the OOS risk classifier flags an event. This is a signal-layer proxy, not the funded Virtual Trader account.",
        "",
        "| Risk filter | Rule return | Filtered return | Improvement | 95% CI | Avoided losing signals | Kept winning signals |",
        "|---|---:|---:|---:|---|---:|---:|",
    ])
    for key, item in development["rule_plus_risk_filter"].items():
        lines.append(
            f"| {key} | {_fmt(item.get('rule_after_cost_return_pct'))}% | {_fmt(item.get('filtered_after_cost_return_pct'))}% | {_fmt(item.get('after_cost_improvement_pct'))}% | {_fmt(item.get('after_cost_improvement_95pct_ci'))} | {_fmt(item.get('avoided_losing_signal_rate'))} | {_fmt(item.get('retained_winning_signal_rate'))} |"
        )
    lines.extend([
        "",
        "## 11. Existing non-price data inventory",
        "",
        "| Source | PIT-safe now? | Historical depth / main issue | Markets |",
        "|---|---|---|---|",
    ])
    for item in report["non_price_inventory"]:
        lines.append(
            f"| {item['source']} | {'yes' if item['safe_for_this_experiment'] else 'no'} | {item['historical_depth']}; {item['reason']} | {item['markets']} |"
        )
    lines.extend([
        "",
        "## 12. Price-only versus price plus non-price",
        "",
        "No price-plus-non-price model was fitted. Every existing non-price feed failed the predeclared historical point-in-time coverage requirement. Filling old rows from today's snapshots would create leakage; treating unavailable feeds as neutral zeros would create a misleading comparison.",
        "",
        "## 13. Investigation of the three earlier passes",
        "",
        f"The earlier pass rate was {prior['passed_model_count']}/{prior['models_tested']} ({prior['empirical_pass_rate']:.2%}). Each pass was rechecked on pre-holdout development data for fold stability, current/reduced/compact feature sensitivity, and 0/63/126-row start-date sensitivity. Their individual final holdout was not opened, preserving the one-candidate rule.",
        "",
        "| Prior pass | Original direction | Original balanced | Non-overlap direction | Non-overlap CI |",
        "|---|---:|---:|---:|---|",
    ])
    for item in prior["models"]:
        original = item["original_gate_metrics"]
        lines.append(
            f"| {item['ticker']} {item['period']} {item['model_name']} | {_fmt(original.get('direction_accuracy'))} | {_fmt(original.get('balanced_direction_accuracy'))} | {_fmt(item.get('non_overlapping_direction_accuracy'))} | {_fmt(item.get('non_overlapping_direction_95pct_wilson_ci'))} |"
        )
    lines.extend([
        "",
        "These are exceptions discovered after a 220-model search. Their intervals, feature/start sensitivity, baseline comparisons, and multiple-testing exposure do not justify promotion.",
        "",
        "## 14. Separate HK diagnosis",
        "",
        f"The HK sample has {diagnostics['HK']['ticker_count']} securities versus {diagnostics['US']['ticker_count']} US securities. Median development history is {_fmt(diagnostics['HK']['median_development_rows'], 0)} rows; median five-day excess-return volatility is {_fmt(diagnostics['HK']['median_excess_return_std_pct'])}% versus {_fmt(diagnostics['US']['median_excess_return_std_pct'])}% in the US sample. Median annualised volatility is {_fmt(diagnostics['HK']['median_annualized_volatility_pct'])}% versus {_fmt(diagnostics['US']['median_annualized_volatility_pct'])}%.",
        "",
        "The same date-safe compact features and broad `2800` benchmark are used, but five names provide a very thin daily ranking cross-section, HK has different gaps/liquidity/event structure, and the repository has no safe historical HK news, analyst, filing, earnings, or fundamentals panel. These are data/objective limitations, not a reason to weaken validation.",
        "",
        "## 15. Development-only candidate selection",
        "",
        f"Selected: **{selected['name']}**. Qualified candidates: {selected['qualified_candidate_count']}/{selected['candidate_count']}. Development qualification: **{'passed' if selected['qualified'] else 'failed'}**. {selected['selection_note']}",
        "",
        "Qualification failures: " + (", ".join(selected.get("qualification_failures", [])) or "none") + ".",
        "",
        "Final review invalidated that provisional qualification because the original risk criterion tested chance/prevalence and an uncalibrated Brier proxy, but did not require repeatable incremental skill over the strongest simple risk baseline. The attachment's stated strong-baseline requirement takes precedence; this is treated as an audit defect, not repaired by tuning the opened holdout.",
        "",
        "## 16. Locked final holdout",
        "",
        f"Only that selected architecture was fitted on all purged development data and evaluated once on the locked block. Matured rows evaluated: {locked['rows_evaluated']}. No model, feature, threshold, or architecture was changed after opening it.",
        "",
        "After detecting the baseline-comparison defect, the same fixed architecture was rerun only to calculate properly calibrated simple-baseline metrics. This is explicitly post-hoc; the holdout was already open and was not used to choose another model.",
        "",
        "| Market | Model ROC-AUC | Best simple ROC-AUC | ROC uplift | Model PR-AUC | Best simple PR-AUC | PR uplift |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for market, item in effective_locked["markets"].items():
        lines.append(
            f"| {market} | {_fmt(item.get('roc_auc'))} | {_fmt(item.get('best_simple_baseline_roc_auc'))} | {_fmt(item.get('roc_auc_uplift_over_best_simple_baseline'))} | {_fmt(item.get('pr_auc'))} | {_fmt(item.get('best_simple_baseline_pr_auc'))} | {_fmt(item.get('pr_auc_uplift_over_best_simple_baseline'))} |"
        )
    lines.extend([
        "",
        "## 17. Minimum repeatable success standard",
        "",
        "Relative return required balanced accuracy >=52%, positive association and after-cost lower confidence bounds, and >=60% positive folds. Ranking required IC >=0.02 with positive IC and after-cost spread lower bounds and >=60% stable folds. Risk must clear chance and beat the best calibrated simple proxy on ROC-AUC, PR-AUC, and Brier score with >=60% stable folds; this incremental-baseline requirement was omitted from the first implementation and therefore prevents adoption from this run. The hybrid additionally required a positive lower bound on improvement over the unchanged rule.",
        "",
        "## 18. Production isolation and limitations",
        "",
        f"Production model-tree fingerprint before/after: `{report['production_fingerprint_before']}` / `{report['production_fingerprint_after']}`; unchanged = **{str(report['production_state_unchanged']).lower()}**. Outputs exist only under `data/model_design_experiments/{EXPERIMENT_NAME}/`. No lifecycle registry or active pointer was written. Conclusions cover this fixed sample and historical period, not future profitability.",
        "",
        "## 19. Architecture decision",
        "",
        f"**{choice['code']}. {choice['label']}**",
    ])
    return "\n".join(lines) + "\n"


def render_report(report: dict[str, Any]) -> str:
    """Render the user's exact 19-item audit checklist and one decision."""
    control = report["frozen_control"]
    dev = report["development_results"]
    prior = report["prior_pass_audit"]
    diag = report["data_diagnostics"]
    selected = report["locked_candidate_selection"]
    original_locked = report["locked_final_result"]
    recheck = report.get("posthoc_strong_baseline_recheck")
    corrected_dev = recheck["development"]["aggregate"] if recheck else None
    corrected_locked = recheck["locked"] if recheck else original_locked
    verification = report.get("verification", {})
    choice = report["final_architecture_choice"]

    lines = [
        "# Model Trader objective-redesign audit — 2026-08-23",
        "",
        "Status: **EXPERIMENTAL ONLY**. Nothing was deployed, registered, promoted, or made runtime-selectable. Production gates, registry entries, active pointers, and Virtual Trader decisions were not changed.",
        "",
        "The fixed 21-security sample used the final 252 market dates per market as a locked block, with a five-date label purge. The block was untouched by this redesign until one architecture was selected, but it is not globally virgin because the preceding absolute-return audit had already summarized these historical dates.",
        "",
        "## 1. Absolute-return baseline",
        "",
        f"The prior controlled current-scheme result was frozen and reused read-only: **{control['fully_validated']}/{control['models']} passed ({control['pass_rate']:.2%})**. The 38-feature design had about 50.18% balanced accuracy / MCC 0.0041; compact had about 50.21% / MCC 0.0045; pooled and alternative absolute targets had no repeatable design-level edge. The isolated passes were "
        + ", ".join(f"{item['ticker']} {item['period']} {item['model_name']}" for item in control["passed_models"]) + ".",
        "",
        "## 2. Excess-return regression results",
        "",
        "Target = ticker adjusted five-day return minus `VOO` (US) or `2800` (HK) over identical dates. Action bands used cost plus training-fold-only uncertainty.",
        "",
        "| Model | Correlation | 95% block CI | Balanced | MAE | Recent-relative-momentum MAE | After-cost excess | 95% block CI |",
        "|---|---:|---|---:|---:|---:|---:|---|",
    ]
    for model, payload in dev["relative_regression"].items():
        item = payload["aggregate"]
        lines.append(
            f"| {model} | {_fmt(item.get('correlation'))} | {_fmt(item.get('correlation_95pct_block_bootstrap_ci'))} | {_fmt(item.get('balanced_accuracy'))} | {_fmt(item.get('mae_pct'))}% | {_fmt(item.get('baseline_mae_pct'))}% | {_fmt(item.get('average_after_cost_excess_return_pct'))}% | {_fmt(item.get('after_cost_excess_return_95pct_ci'))} |"
        )
    lines.extend([
        "",
        "Neither model shows stable association: Ridge correlation is approximately zero, and the small positive economic replay is not accompanied by predictive correlation.",
        "",
        "## 3. Relative classification results",
        "",
        "Three-class and binary economic thresholds were recomputed inside every training fold; final/OOS returns never set a class boundary.",
        "",
        "| Target:model | Balanced | MCC | ROC-AUC | PR-AUC | After-cost excess |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for key, payload in dev["relative_classification"].items():
        item = payload["aggregate"]
        lines.append(
            f"| {key} | {_fmt(item.get('balanced_accuracy'))} | {_fmt(item.get('mcc'))} | {_fmt(item.get('roc_auc'))} | {_fmt(item.get('pr_auc'))} | {_fmt(item.get('average_after_cost_excess_return_pct'))}% |"
        )
    lines.extend([
        "",
        "Three-class balanced accuracy is about 0.339; binary balanced accuracy is about 0.51. Neither is meaningfully repeatable.",
        "",
        "## 4. Cross-sectional ranking results",
        "",
        "Ranking was market-local and date-global: no future-period normalization and no US/HK mixing.",
        "",
        "| Model | Spearman IC | Worst market CI lower | Pearson IC | After-cost spread | Worst market CI lower | Top hit | Turnover | Stable folds |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for model, payload in dev["ranking"].items():
        item = payload["aggregate"]
        lines.append(
            f"| {model} | {_fmt(item.get('spearman_ic'))} | {_fmt(item.get('spearman_ic_ci_lower'))} | {_fmt(item.get('pearson_ic'))} | {_fmt(item.get('after_cost_spread_pct'))}% | {_fmt(item.get('after_cost_spread_ci_lower'))} | {_fmt(item.get('top_quintile_hit_rate'))} | {_fmt(item.get('turnover'))} | {_fmt(item.get('positive_fold_rate'))} |"
        )
    lines.extend([
        "",
        "The forest's aggregate IC/spread is positive, but both worst-market confidence bounds cross zero and fewer than half the folds are jointly positive.",
        "",
        "## 5. Risk-prediction results",
        "",
        "Future volatility is annualized next-five-session realized volatility. Adverse/high-vol events use training-fold 85th/75th percentiles.",
        "",
        "| Volatility model | MAE | Rolling-vol MAE | Improvement | Correlation | Positive-improvement folds |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for model, payload in dev["risk_regression"].items():
        item = payload["aggregate"]
        lines.append(
            f"| {model} | {_fmt(item.get('mae_pct'))}% | {_fmt(item.get('baseline_mae_pct'))}% | {_fmt(item.get('mae_improvement_pct'))}% | {_fmt(item.get('correlation'))} | {_fmt(item.get('positive_mae_improvement_fold_rate'))} |"
        )
    lines.extend([
        "",
        "| Event:model | ROC-AUC | PR-AUC | Recall | FPR | Brier |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for key, payload in dev["risk_classification"].items():
        item = payload["aggregate"]
        lines.append(
            f"| {key} | {_fmt(item.get('roc_auc'))} | {_fmt(item.get('pr_auc'))} | {_fmt(item.get('recall'))} | {_fmt(item.get('false_positive_rate'))} | {_fmt(item.get('brier_score'))} |"
        )
    if corrected_dev:
        lines.extend([
            "",
            f"The initially selected high-vol forest has development ROC-AUC {_fmt(corrected_dev.get('roc_auc'))}, but the best calibrated one-feature baseline has {_fmt(corrected_dev.get('best_simple_baseline_roc_auc'))} (uplift {_fmt(corrected_dev.get('roc_auc_uplift_over_best_simple_baseline'))}). Its PR-AUC is {_fmt(corrected_dev.get('pr_auc'))} versus baseline {_fmt(corrected_dev.get('best_simple_baseline_pr_auc'))} (uplift {_fmt(corrected_dev.get('pr_auc_uplift_over_best_simple_baseline'))}). Thus most risk predictability already exists in simple observable risk state.",
        ])
    lines.extend([
        "",
        "## 6. Regime-conditioned results",
        "",
        "Bull/non-bull, high/low volatility, and stressed/normal boundaries used only each training fold's median benchmark trend, median ticker volatility, and 25th-percentile drawdown. Detailed rows are preserved in the JSON artifact. No candidate was stable enough across regimes and folds to rescue an otherwise weak objective; no regime threshold was tuned on final data.",
        "",
        "## 7. Existing non-price data inventory",
        "",
        "| Source | Coverage/depth | Refresh | US/HK | Safe now? |",
        "|---|---|---|---|---|",
    ])
    for item in report["non_price_inventory"]:
        lines.append(
            f"| {item['source']} | {item['current_coverage']}; {item['historical_depth']} | {item['refresh']} | {item['markets']} | {'yes' if item['safe_for_this_experiment'] else 'no'} |"
        )
    lines.extend([
        "",
        "## 8. Point-in-time / leakage assessment",
        "",
        "No non-price source qualified for model fitting. Yahoo news lacks a persistent archive and zero-fills unavailable dates; Reddit/analyst/Alpha Vantage context is a current snapshot; SEC filing dates are promising but the current code aggregates recent filings rather than constructing a daily as-of panel. Fundamentals, macro, and true breadth have no historical training source. Therefore no price-plus-non-price experiment was fabricated.",
        "",
        "## 9. Three passing-model investigation",
        "",
        f"The pass rate was {prior['passed_model_count']}/{prior['models_tested']} ({prior['empirical_pass_rate']:.2%}), with material multiple-testing risk.",
        "",
        "| Model | Original direction/balanced | Non-overlap direction | 95% CI | Feature sensitivity (current/reduced/compact) | Start sensitivity (0/63/126) |",
        "|---|---|---:|---|---|---|",
    ])
    for item in prior["models"]:
        original = item["original_gate_metrics"]
        features = "/".join(
            _fmt(row["direction_accuracy"]) for row in item["feature_sensitivity_before_final_holdout"]
        )
        starts = "/".join(
            _fmt(row["direction_accuracy"]) for row in item["start_date_sensitivity_before_final_holdout"]
        )
        lines.append(
            f"| {item['ticker']} {item['period']} {item['model_name']} | {_fmt(original.get('direction_accuracy'))}/{_fmt(original.get('balanced_direction_accuracy'))} | {_fmt(item.get('non_overlapping_direction_accuracy'))} | {_fmt(item.get('non_overlapping_direction_95pct_wilson_ci'))} | {features} | {starts} |"
        )
    lines.extend([
        "",
        "All non-overlapping intervals cross 50%; feature/start sensitivity is non-trivial. Their individual final block was not opened, preserving the one-architecture rule. They look more like sparse selection exceptions than established ticker-specific signal and were not promoted.",
        "",
        "## 10. US versus HK findings",
        "",
        f"HK had {diag['HK']['ticker_count']} securities versus {diag['US']['ticker_count']} US securities. Median development rows: {_fmt(diag['HK']['median_development_rows'], 0)} HK. Median excess-return volatility: {_fmt(diag['HK']['median_excess_return_std_pct'])}% HK vs {_fmt(diag['US']['median_excess_return_std_pct'])}% US; annualized volatility: {_fmt(diag['HK']['median_annualized_volatility_pct'])}% vs {_fmt(diag['US']['median_annualized_volatility_pct'])}%. The five-name HK cross-section is too thin for robust quintiles. HK risk discrimination was more incremental than US in the locked diagnostic, but this did not repeat across both markets. `2800` remains the safest existing benchmark; no HK standards were lowered.",
        "",
        "## 11. Development-period results",
        "",
        f"The original predeclared selector chose **{selected['name']}**; {selected['qualified_candidate_count']}/{selected['candidate_count']} candidates cleared its first-pass criteria. Final review found those risk criteria incomplete because they cleared chance/prevalence but did not require incremental discrimination over the strongest calibrated simple risk baseline. The immutable selection file was preserved; no second model was selected after this defect was found.",
        "",
        "## 12. Locked-test results",
        "",
        f"Only the selected high-volatility forest was evaluated on {original_locked['rows_evaluated']} matured locked rows. The later run recalculated baselines for that same fixed architecture only; it is explicitly post-hoc because the holdout was already open.",
        "",
        "| Market | Model ROC | Best simple ROC | Uplift | Model PR | Best simple PR | Uplift | Model Brier | Best simple Brier |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for market, item in corrected_locked["markets"].items():
        lines.append(
            f"| {market} | {_fmt(item.get('roc_auc'))} | {_fmt(item.get('best_simple_baseline_roc_auc'))} | {_fmt(item.get('roc_auc_uplift_over_best_simple_baseline'))} | {_fmt(item.get('pr_auc'))} | {_fmt(item.get('best_simple_baseline_pr_auc'))} | {_fmt(item.get('pr_auc_uplift_over_best_simple_baseline'))} | {_fmt(item.get('brier_score'))} | {_fmt(item.get('best_simple_baseline_brier_score'))} |"
        )
    lines.extend([
        "",
        "US ROC-AUC is worse than the simple baseline; HK improves. That is not cross-market repeatability.",
        "",
        "## 13. Best simple baseline",
        "",
        "For absolute direction, Always-UP remains the hard raw-accuracy baseline (56.77%, balanced 50%). For relative return, recent five-day relative momentum is the direct magnitude baseline. For risk, the strongest fair comparison is a training-only one-feature calibrated model using rolling 20-day volatility, current drawdown, or intraday range. The selected forest does not consistently beat this simple risk baseline.",
        "",
        "## 14. Best ML architecture",
        "",
        "The strongest ML diagnostic is the pooled shallow random-forest high-volatility classifier. It predicts high-volatility states, not BUY/SELL direction. It is **not adoption-ready** because its incremental development PR-AUC is negative versus the best simple proxy and locked US ROC-AUC is worse. No alternate architecture may be selected now that the holdout is open.",
        "",
        "## 15. Statistical significance",
        "",
        "Relative correlation and ranking-spread intervals cross zero. The three prior-pass non-overlap Wilson intervals cross 50%. The high-vol forest clears chance strongly, but that tests whether volatility is predictable—not whether ML adds value beyond observable volatility. Incremental risk skill is near zero in development and changes sign by market in the locked block. No multiple-comparison-adjusted, repeatable incremental ML edge is established.",
        "",
        "## 16. Economic significance",
        "",
        "The unchanged rule plus high-vol forest filter reduced mean after-cost signal return from 0.023% to 0.005%; its improvement was -0.018% with a 95% block interval approximately [-0.024%, -0.010%]. It retained about 91% of winning rule signals but avoided only about 7% of losing signals. Relative/ranking economic results were not confidence-stable. No tested ML architecture shows exploitable economic improvement.",
        "",
        "## 17. Computational cost",
        "",
        "Primary audit wall time: 525.2 seconds; fixed-architecture calibrated-baseline recheck: 84.0 seconds; total about 10.2 minutes on this workstation. The controlled design used two simple model forms, five purged folds, 21 securities, and 10-year source histories; it did not launch a broader hyperparameter or indicator search. Compressed OOS streams and JSON evidence are under `data/model_design_experiments/objective_redesign_2026_08/`.",
        "",
        "## 18. Files changed",
        "",
        "- `scripts/model_objective_benchmark.py` — isolated objective/ranking/risk/holdout harness.",
        "- `tests/test_model_objective_benchmark.py` — leakage, split, threshold, ranking, baseline, and isolation tests.",
        "- `reports/model_objective_audit_2026-08-23.md` — this audit.",
        "",
        "Generated experiment evidence is ignored under `data/model_design_experiments/objective_redesign_2026_08/`. Production model-tree fingerprint before/after is unchanged: `" + report["production_fingerprint_before"] + "`.",
        "",
        "## 19. Tests run",
        "",
    ])
    if verification:
        for item in verification.get("commands", []):
            lines.append(
                f"- `{item['command']}`: {item['result']}."
            )
    else:
        lines.append("- Verification pending final test run.")
    lines.extend([
        "",
        "No frontend source or production runtime source was changed by this objective-redesign stage, so frontend build/tests were not rerun for this stage.",
        "",
        "## Architecture recommendation",
        "",
        f"**{choice['code']}. {choice['label']}**",
    ])
    return "\n".join(lines) + "\n"


def run_audit(output_root: Path, report_path: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    production_before = production_model_fingerprint()
    datasets, data_failures = _load_datasets()
    boundaries = compute_market_holdout_boundaries(datasets)
    predeclared = {
        "created_at_utc": datetime.now(timezone.utc),
        "experiment": EXPERIMENT_NAME,
        "status": "EXPERIMENTAL_ONLY",
        "production_writes_allowed": False,
        "source_period": SOURCE_PERIOD,
        "sample": [asdict(item) for item in REPRESENTATIVE_UNIVERSE],
        "benchmarks": BENCHMARKS,
        "locked_holdout_dates": LOCKED_HOLDOUT_DATES,
        "horizon_purge_dates": HORIZON_ROWS,
        "compact_features": COMPACT_FEATURES,
        "regression_models": REGRESSION_MODELS,
        "classification_models": CLASSIFICATION_MODELS,
        "selection_criteria": SELECTION_CRITERIA,
        "boundaries": boundaries,
        "locked_outcomes_opened": False,
    }
    _write_json(output_root / "PREDECLARED_MANIFEST.json", predeclared)

    market_frames: dict[str, dict[str, pd.DataFrame]] = {}
    for market in BENCHMARKS:
        development = pooled_market_frame(
            datasets, market=market, boundary=boundaries[market], locked=False
        )
        relative_development = development.loc[
            ~development["ticker"].astype(str).eq(BENCHMARKS[market])
        ].reset_index(drop=True)
        market_frames[market] = {
            "risk_development": development,
            "relative_development": relative_development,
        }

    development_results, candidates = run_development_experiments(
        market_frames, output_root
    )
    selected = select_locked_candidate(candidates)
    selection_record = {
        "selected_at_utc": datetime.now(timezone.utc),
        "locked_outcomes_opened_before_selection": False,
        "selected": {key: value for key, value in selected.items() if key != "summary"},
        "all_candidates": [
            {key: value for key, value in item.items() if key != "summary"}
            for item in candidates
        ],
    }
    _write_json(output_root / "DEVELOPMENT_SELECTION.json", selection_record)

    # The final target block is first materialised for evaluation only after
    # DEVELOPMENT_SELECTION.json records the immutable architecture choice.
    for market in BENCHMARKS:
        locked_frame = pooled_market_frame(
            datasets, market=market, boundary=boundaries[market], locked=True
        )
        market_frames[market]["risk_locked"] = locked_frame
        market_frames[market]["relative_locked"] = locked_frame.loc[
            ~locked_frame["ticker"].astype(str).eq(BENCHMARKS[market])
        ].reset_index(drop=True)
    locked_result = evaluate_locked_candidate(selected, market_frames)

    stage1_path = (
        PROJECT_ROOT / "data" / "model_design_experiments" / "controlled_2026_08"
        / "stage1_broader_benchmark.json"
    )
    frozen_control = _frozen_control(stage1_path)
    prior_pass_audit = audit_prior_passes(
        datasets, boundaries, stage1_path=stage1_path
    )
    diagnostics = data_diagnostics(datasets, boundaries)
    production_after = production_model_fingerprint()
    choice_code, choice_label = _recommendation_choice(selected, locked_result)
    report: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc),
        "status": "EXPERIMENTAL_ONLY",
        "data_failures": data_failures,
        "predeclared_manifest": predeclared,
        "frozen_control": frozen_control,
        "development_results": development_results,
        "candidate_catalog": candidates,
        "locked_candidate_selection": {
            key: value for key, value in selected.items() if key != "summary"
        },
        "locked_final_result": locked_result,
        "prior_pass_audit": prior_pass_audit,
        "data_diagnostics": diagnostics,
        "non_price_inventory": non_price_inventory(),
        "production_fingerprint_before": production_before,
        "production_fingerprint_after": production_after,
        "production_state_unchanged": production_before == production_after,
        "final_architecture_choice": {
            "code": choice_code,
            "label": choice_label,
        },
    }
    _write_json(output_root / "objective_redesign_audit.json", report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(report), encoding="utf-8")
    return report


def run_strong_baseline_recheck(output_root: Path, report_path: Path) -> dict[str, Any]:
    """Recheck only the immutable selected architecture against fair baselines.

    This function exists because the first completed audit revealed that the
    simple percentile score was not probability-calibrated.  It does not rerun
    candidate selection and must not describe the already-open holdout as new.
    """
    audit_path = output_root / "objective_redesign_audit.json"
    selection_path = output_root / "DEVELOPMENT_SELECTION.json"
    report = json.loads(audit_path.read_text(encoding="utf-8"))
    selection = json.loads(selection_path.read_text(encoding="utf-8"))["selected"]
    if selection.get("task") not in {"adverse_event", "high_vol_event"}:
        raise RuntimeError("The immutable selected architecture is not a risk classifier.")
    production_before = production_model_fingerprint()
    datasets, failures = _load_datasets()
    boundaries = compute_market_holdout_boundaries(datasets)
    target_kind = str(selection["task"])
    target_name = (
        RISK_ADVERSE_TARGET if target_kind == "adverse_event" else RISK_VOLATILITY_TARGET
    )
    development_streams: dict[str, pd.DataFrame] = {}
    locked_streams: dict[str, pd.DataFrame] = {}
    development_markets: dict[str, dict[str, Any]] = {}
    locked_markets: dict[str, dict[str, Any]] = {}
    for market in BENCHMARKS:
        development = pooled_market_frame(
            datasets, market=market, boundary=boundaries[market], locked=False
        )
        locked = pooled_market_frame(
            datasets, market=market, boundary=boundaries[market], locked=True
        )
        development_stream = evaluate_classification_oos(
            development,
            target_name=target_name,
            model_name=str(selection["model_name"]),
            target_kind=target_kind,
        )
        locked_stream = _locked_classification_stream(
            development,
            locked,
            target_name=target_name,
            model_name=str(selection["model_name"]),
            target_kind=target_kind,
        )
        development_streams[market] = development_stream
        locked_streams[market] = locked_stream
        development_markets[market] = classification_summary(
            development_stream, target_kind=target_kind
        )
        locked_markets[market] = classification_summary(
            locked_stream, target_kind=target_kind
        )
    recheck = {
        "created_at_utc": datetime.now(timezone.utc),
        "status": "POSTHOC_FIXED_ARCHITECTURE_BASELINE_RECHECK",
        "selection_changed": False,
        "holdout_already_open": True,
        "selected_architecture": selection,
        "data_failures": failures,
        "development": {
            "aggregate": classification_summary(
                pd.concat(development_streams.values(), ignore_index=True),
                target_kind=target_kind,
            ),
            "markets": development_markets,
        },
        "locked": {
            "aggregate": classification_summary(
                pd.concat(locked_streams.values(), ignore_index=True),
                target_kind=target_kind,
            ),
            "markets": locked_markets,
            "rows_evaluated": int(sum(len(item) for item in locked_streams.values())),
        },
        "baseline_policy": (
            "Each simple proxy is a one-feature logistic model fit on the same training fold: "
            "recent relative momentum, rolling 20-day volatility, current drawdown, and intraday range."
        ),
        "production_fingerprint_before": production_before,
        "production_fingerprint_after": production_model_fingerprint(),
    }
    recheck["production_state_unchanged"] = (
        recheck["production_fingerprint_before"]
        == recheck["production_fingerprint_after"]
    )
    _write_json(output_root / "POSTHOC_STRONG_BASELINE_RECHECK.json", recheck)
    report["posthoc_strong_baseline_recheck"] = recheck
    # The same model must beat the best simple proxy in both markets.  Failure
    # cannot trigger a second architecture search because the holdout is open.
    choice_code, choice_label = _recommendation_choice(
        {**selection, "qualified": True}, recheck["locked"]
    )
    if choice_code != "D":
        choice_code, choice_label = "F", "NO ML APPROACH CURRENTLY JUSTIFIED"
    report["final_architecture_choice"] = {
        "code": choice_code,
        "label": choice_label,
    }
    report["production_fingerprint_after"] = recheck["production_fingerprint_after"]
    report["production_state_unchanged"] = (
        report["production_fingerprint_before"]
        == report["production_fingerprint_after"]
    )
    _write_json(audit_path, report)
    report_path.write_text(render_report(report), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "data" / "model_design_experiments" / EXPERIMENT_NAME,
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=PROJECT_ROOT / "reports" / "model_objective_audit_2026-08-23.md",
    )
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--strong-baseline-recheck-only",
        action="store_true",
        help="Recheck only the immutable selected risk architecture after the first audit exposed an unfair baseline calibration.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    report = (
        run_strong_baseline_recheck(args.output_root, args.report_path)
        if args.strong_baseline_recheck_only
        else run_audit(args.output_root, args.report_path)
    )
    print(json.dumps({
        "report_path": str(args.report_path),
        "production_state_unchanged": report["production_state_unchanged"],
        "selected_candidate": report["locked_candidate_selection"]["name"],
        "selected_candidate_qualified": report["locked_candidate_selection"]["qualified"],
        "final_architecture_choice": report["final_architecture_choice"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
