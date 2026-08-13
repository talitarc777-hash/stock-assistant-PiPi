"""Baseline model training utilities for stock research datasets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import pickle
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import (
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    recall_score,
    r2_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit

from app.core.settings import get_settings
from app.services.prediction_explanations import build_prediction_explanation
from app.services.research_pipeline import build_feature_dataset
from app.services.market_config import model_security_root, resolve_model_identity, resolve_security
from app.services.market_regime import assess_market_regime
from app.services.outperformance_economics import evaluate_outperformance_economics
from app.services.research_pipeline import OUTPERFORMANCE_ROUND_TRIP_COST_PCT

logger = logging.getLogger(__name__)


class ModelTrainingError(Exception):
    """Raised when baseline model training cannot proceed."""


@dataclass(frozen=True)
class TrainingArtifact:
    """Saved artifact paths for one trained model."""

    ticker: str
    period: str
    target_name: str
    model_name: str
    model_path: Path
    feature_list_path: Path
    metrics_path: Path
    predictions_path: Path
    evaluation_table_path: Path


@dataclass(frozen=True)
class TrainingRunResult:
    """Structured result for one trained baseline model."""

    ticker: str
    period: str
    target_name: str
    model_name: str
    task_type: str
    feature_names: list[str]
    metrics: dict[str, Any]
    predictions: pd.DataFrame
    evaluation_table: pd.DataFrame
    artifact: TrainingArtifact


FEATURE_EXCLUDE_COLUMNS: set[str] = {
    "date",
    "ticker",
    "benchmark",
    "target_5d_return",
    "target_5d_updown",
    "target_20d_regime",
}
VALIDATION_SCHEME_VERSION = 5

POOLED_LEVEL_FEATURES = {
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "sma_20",
    "sma_50",
    "sma_200",
    "ema_12",
    "ema_26",
    "macd_line",
    "macd_signal",
    "macd_histogram",
    "avg_volume_20",
}


def _prepare_pooled_feature_dataset(dataset_df: pd.DataFrame) -> pd.DataFrame:
    """Replace cross-asset price/volume levels with comparable ratios."""
    result = dataset_df.copy()
    close = pd.to_numeric(result["close"], errors="coerce").replace(0, np.nan)
    previous_close = close.groupby(result["ticker"]).shift(1).replace(0, np.nan)
    day_range = (
        pd.to_numeric(result["high"], errors="coerce")
        - pd.to_numeric(result["low"], errors="coerce")
    ).replace(0, np.nan)
    result["overnight_gap_pct"] = (
        pd.to_numeric(result["open"], errors="coerce") / previous_close - 1.0
    ) * 100.0
    result["intraday_range_pct"] = day_range / close * 100.0
    result["close_location_in_range"] = (
        close - pd.to_numeric(result["low"], errors="coerce")
    ) / day_range
    for window in (20, 50, 200):
        result[f"close_vs_sma_{window}_pct"] = (
            close / pd.to_numeric(result[f"sma_{window}"], errors="coerce").replace(0, np.nan)
            - 1.0
        ) * 100.0
    for window in (12, 26):
        result[f"close_vs_ema_{window}_pct"] = (
            close / pd.to_numeric(result[f"ema_{window}"], errors="coerce").replace(0, np.nan)
            - 1.0
        ) * 100.0
    for column in ("macd_line", "macd_signal", "macd_histogram"):
        result[f"{column}_pct"] = pd.to_numeric(result[column], errors="coerce") / close * 100.0
    result["volume_vs_20d_avg"] = (
        pd.to_numeric(result["volume"], errors="coerce")
        / pd.to_numeric(result["avg_volume_20"], errors="coerce").replace(0, np.nan)
    )
    return result.drop(columns=[column for column in POOLED_LEVEL_FEATURES if column in result.columns])


def prepare_pooled_feature_dataset(dataset_df: pd.DataFrame) -> pd.DataFrame:
    """Build the exact scale-independent feature schema used by pooled models.

    This public entry point is shared by training and live inference so a
    validated GLOBAL model never receives a different feature representation.
    """
    return _prepare_pooled_feature_dataset(dataset_df)


def prepare_stationary_feature_dataset(dataset_df: pd.DataFrame) -> pd.DataFrame:
    """Build scale-independent features for any return-regression model."""
    return _prepare_pooled_feature_dataset(dataset_df)


def _target_horizon_rows(target_name: str) -> int:
    """Return the forward-label horizon that must be purged before each test fold."""
    return 20 if str(target_name).strip() == "target_20d_regime" else 5


def _choose_time_series_splits(row_count: int) -> int:
    """Pick a beginner-friendly number of expanding-window validation splits."""
    if row_count < 30:
        raise ModelTrainingError(
            "Not enough rows for time-series validation. Need at least 30 usable rows."
        )
    if row_count >= 250:
        return 5
    if row_count >= 120:
        return 4
    if row_count >= 60:
        return 3
    return 2


def _purged_date_splits(
    date_series: pd.Series,
    *,
    split_count: int,
    gap_rows: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Split by unique market dates so pooled tickers never cross fold boundaries."""
    normalized_dates = pd.to_datetime(date_series, errors="coerce").dt.normalize()
    if normalized_dates.isna().any():
        raise ModelTrainingError("Training dates must be valid for time-series validation.")
    unique_dates = pd.Index(normalized_dates.unique()).sort_values()
    if len(unique_dates) < 30:
        raise ModelTrainingError("Not enough unique market dates for time-series validation.")

    splitter = TimeSeriesSplit(n_splits=split_count, gap=gap_rows)
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    date_values = normalized_dates.to_numpy()
    for train_date_index, test_date_index in splitter.split(unique_dates):
        train_dates = unique_dates.take(train_date_index).to_numpy()
        test_dates = unique_dates.take(test_date_index).to_numpy()
        train_rows = np.flatnonzero(np.isin(date_values, train_dates))
        test_rows = np.flatnonzero(np.isin(date_values, test_dates))
        splits.append((train_rows, test_rows))
    return splits


def _build_feature_frame(
    dataset_df: pd.DataFrame,
    target_name: str,
) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series, list[str]]:
    """Select numeric model features and align them with one target column."""
    if target_name not in dataset_df.columns:
        raise ModelTrainingError(f"Target column not found: {target_name}")

    numeric_columns = dataset_df.select_dtypes(include=[np.number, "bool"]).columns.tolist()
    feature_columns = [
        column for column in numeric_columns
        if column not in FEATURE_EXCLUDE_COLUMNS
        and not column.startswith("target_")
    ]

    if not feature_columns:
        raise ModelTrainingError("No numeric feature columns available for training.")

    identity_columns = ["date"] + (["ticker"] if "ticker" in dataset_df.columns else [])
    training_df = dataset_df[identity_columns + [target_name] + feature_columns].copy()
    # Keep rows that have a target label and let the model pipeline's imputer handle
    # missing feature values. Dropping on *all* feature NaNs can wipe out the dataset,
    # especially when optional features (like news sentiment) are sparse.
    training_df = training_df.dropna(subset=[target_name])

    if training_df.empty:
        raise ModelTrainingError(f"No rows remain after cleaning for target {target_name}.")

    x_frame = training_df[feature_columns]
    y_series = training_df[target_name]
    date_series = pd.to_datetime(training_df["date"], errors="coerce")
    source_ticker_series = training_df.get(
        "ticker",
        pd.Series([""] * len(training_df), index=training_df.index),
    ).astype(str)
    return x_frame, y_series, date_series, source_ticker_series, feature_columns


def _build_classifier_pipeline(model_name: str) -> Pipeline:
    """Return one transparent baseline classifier pipeline."""
    model_key = model_name.strip().lower()

    if model_key == "logistic_regression":
        estimator = LogisticRegression(max_iter=1000, random_state=42)
        return Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("scaler", StandardScaler()),
                ("model", estimator),
            ]
        )
    if model_key == "random_forest":
        estimator = RandomForestClassifier(
            n_estimators=200,
            max_depth=6,
            random_state=42,
        )
        return Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("model", estimator),
            ]
        )
    if model_key == "gradient_boosting":
        estimator = GradientBoostingClassifier(random_state=42)
        return Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("model", estimator),
            ]
        )

    raise ModelTrainingError(f"Unsupported classification model: {model_name}")


def _build_regressor_pipeline(model_name: str) -> Pipeline:
    """Return one transparent baseline regressor pipeline."""
    model_key = model_name.strip().lower()

    if model_key == "linear_regression":
        estimator = LinearRegression()
        return Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("scaler", StandardScaler()),
                ("model", estimator),
            ]
        )
    if model_key == "ridge_regression":
        # Regularization makes the correlated technical indicators less likely
        # to produce unstable coefficients, which is especially useful for the
        # smaller per-security HK datasets.
        estimator = Ridge(alpha=10.0)
        return Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("scaler", StandardScaler()),
                ("model", estimator),
            ]
        )
    if model_key == "random_forest":
        estimator = RandomForestRegressor(
            n_estimators=200,
            max_depth=6,
            random_state=42,
        )
        return Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("model", estimator),
            ]
        )
    if model_key == "gradient_boosting":
        estimator = GradientBoostingRegressor(random_state=42)
        return Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("model", estimator),
            ]
        )

    raise ModelTrainingError(f"Unsupported regression model: {model_name}")


def _get_default_model_names(task_type: str) -> list[str]:
    """Return the baseline model list for a task type."""
    if task_type == "classification":
        return ["logistic_regression", "random_forest", "gradient_boosting"]
    if task_type == "regression":
        return ["linear_regression", "ridge_regression", "random_forest", "gradient_boosting"]
    raise ModelTrainingError(f"Unsupported task type: {task_type}")


def _score_classifier_predictions(y_true: pd.Series, y_pred: np.ndarray) -> dict[str, Any]:
    """Compute compact classification metrics from out-of-sample predictions."""
    y_true_int = y_true.astype(int)
    y_pred_int = pd.Series(y_pred, index=y_true.index).astype(int)

    return {
        "accuracy": float(accuracy_score(y_true_int, y_pred_int)),
        "precision": float(precision_score(y_true_int, y_pred_int, zero_division=0)),
        "recall": float(recall_score(y_true_int, y_pred_int, zero_division=0)),
        "f1": float(f1_score(y_true_int, y_pred_int, zero_division=0)),
        "positive_rate_actual": float(y_true_int.mean()),
        "positive_rate_predicted": float(y_pred_int.mean()),
    }


def _score_regression_predictions(y_true: pd.Series, y_pred: np.ndarray) -> dict[str, Any]:
    """Compute compact regression metrics from out-of-sample predictions."""
    rmse = mean_squared_error(y_true, y_pred) ** 0.5
    direction_accuracy = float(((y_true > 0) == (pd.Series(y_pred, index=y_true.index) > 0)).mean())
    absolute_errors = (
        y_true.astype(float) - pd.Series(y_pred, index=y_true.index).astype(float)
    ).abs()

    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(rmse),
        "r2": float(r2_score(y_true, y_pred)),
        "direction_accuracy": direction_accuracy,
        "absolute_error_80_pct": float(absolute_errors.quantile(0.80)),
        "absolute_error_95_pct": float(absolute_errors.quantile(0.95)),
    }


def _calibrate_regression_abstention_threshold(
    actual: np.ndarray,
    predicted: np.ndarray,
) -> float | None:
    """Choose a signal-size threshold on an inner, time-ordered holdout.

    Candidate thresholds are judged only on the calibration slice, never on
    the outer walk-forward test fold.  The score rewards balanced directional
    accuracy and signed return while requiring useful signal coverage.  This
    replaces the old error-quantile rule, which could leave a two-year model
    with too few independent signals to ever pass validation.
    """
    actual_values = np.asarray(actual, dtype=float)
    predicted_values = np.asarray(predicted, dtype=float)
    finite = np.isfinite(actual_values) & np.isfinite(predicted_values)
    actual_values = actual_values[finite]
    predicted_values = predicted_values[finite]
    # The first outer fold of a two-year model can have a short inner holdout.
    # Ten observations are enough to compare the deliberately small threshold
    # grid without making that fold permanently uncalibratable.
    if len(actual_values) < 10:
        return None

    magnitudes = np.abs(predicted_values)
    candidates = sorted({
        0.0,
        *(
            float(np.quantile(magnitudes, quantile))
            for quantile in (0.35, 0.50, 0.65, 0.75)
        ),
    })
    minimum_signals = max(10, int(np.ceil(len(actual_values) * 0.20)))
    best: tuple[float, float] | None = None
    for threshold in candidates:
        active = magnitudes >= threshold
        if int(active.sum()) < minimum_signals:
            continue
        actual_up = actual_values[active] > 0
        predicted_up = predicted_values[active] > 0
        positive_recall = (
            float((predicted_up & actual_up).sum()) / float(actual_up.sum())
            if actual_up.any()
            else 0.0
        )
        actual_down = ~actual_up
        negative_recall = (
            float((~predicted_up & actual_down).sum()) / float(actual_down.sum())
            if actual_down.any()
            else 0.0
        )
        balanced_accuracy = (positive_recall + negative_recall) / 2.0
        direction_accuracy = float((predicted_up == actual_up).mean())
        signed_return = np.sign(predicted_values[active]) * actual_values[active]
        return_component = float(np.tanh(float(np.mean(signed_return)) / 2.0))
        coverage = float(active.mean())
        score = (
            0.55 * balanced_accuracy
            + 0.30 * direction_accuracy
            + 0.10 * max(-1.0, min(1.0, return_component))
            + 0.05 * coverage
        )
        candidate = (score, -threshold)
        if best is None or candidate > best:
            best = candidate
    return -best[1] if best is not None else None


def _get_prediction_confidence(
    fitted_pipeline: Pipeline,
    x_test: pd.DataFrame,
    task_type: str,
    predictions: np.ndarray,
) -> list[float | None]:
    """Return optional confidence values when the model exposes them."""
    if task_type != "classification":
        return [None] * len(predictions)

    model = fitted_pipeline
    if hasattr(model, "predict_proba"):
        try:
            probabilities = model.predict_proba(x_test)
            if probabilities.ndim == 2 and probabilities.shape[1] >= 2:
                return [float(max(row)) for row in probabilities]
        except Exception:  # pragma: no cover - estimator-specific behavior
            logger.debug("predict_proba unavailable for model during walk-forward evaluation")

    return [None] * len(predictions)


def _build_walk_forward_evaluation_frame(
    date_series: pd.Series,
    ticker: str,
    feature_frame: pd.DataFrame,
    y_true: pd.Series,
    y_pred: np.ndarray,
    confidence_scores: list[float | None],
    model_name: str,
    target_name: str,
    task_type: str,
    fold_number: int,
    prediction_uncertainty_pct: float | None = None,
    source_ticker_series: pd.Series | None = None,
) -> pd.DataFrame:
    """Build a chart-friendly walk-forward evaluation table.

    We keep the rows strictly time-ordered and out-of-sample only.
    Random train/test shuffling is inappropriate for time series because it mixes
    future observations into the training set and makes the evaluation unrealistically optimistic.
    """
    actual_series = pd.Series(y_true).reset_index(drop=True)
    predicted_series = pd.Series(y_pred).reset_index(drop=True)
    feature_work_df = feature_frame.reset_index(drop=True)
    source_tickers = (
        source_ticker_series.reset_index(drop=True).astype(str)
        if source_ticker_series is not None
        else pd.Series([ticker] * len(actual_series))
    )

    if task_type == "classification":
        hit_miss = (actual_series.astype(int) == predicted_series.astype(int)).map(
            lambda is_hit: "hit" if is_hit else "miss"
        )
    else:
        hit_miss = ((actual_series > 0) == (predicted_series > 0)).map(
            lambda is_hit: "hit" if is_hit else "miss"
        )

    rows: list[dict[str, Any]] = []
    for index in range(len(actual_series)):
        confidence_score = confidence_scores[index]
        explanation_payload = build_prediction_explanation(
            feature_row=feature_work_df.iloc[index],
            task_type=task_type,
            predicted_value=predicted_series.iloc[index],
            confidence_score=confidence_score,
        )
        market_regime = assess_market_regime(feature_work_df.iloc[index])
        rows.append(
            {
                "prediction_date": pd.to_datetime(date_series).dt.strftime("%Y-%m-%d").iloc[index],
                "ticker": ticker,
                "source_ticker": source_tickers.iloc[index] or ticker,
                "predicted_value": predicted_series.iloc[index],
                "confidence_score": confidence_score,
                "actual_future_result": actual_series.iloc[index],
                "hit_miss": hit_miss.iloc[index],
                "model_name": model_name,
                "target_name": target_name,
                "task_type": task_type,
                "evaluation_window": fold_number,
                "prediction_uncertainty_pct": prediction_uncertainty_pct,
                "is_actionable_signal": (
                    abs(float(predicted_series.iloc[index])) >= prediction_uncertainty_pct
                    if task_type == "regression"
                    and prediction_uncertainty_pct is not None
                    and prediction_uncertainty_pct > 0
                    else True
                ),
                "is_regime_trade_allowed": bool(market_regime["new_position_allowed"]),
                "market_regime_level": market_regime["level"],
                "market_regime_position_multiplier": market_regime[
                    "position_size_multiplier"
                ],
                "market_regime_reasons": ",".join(market_regime["reasons"]),
                "technical_state_summary": explanation_payload["technical_state_summary"],
                "news_sentiment_summary": explanation_payload["news_sentiment_summary"],
                "benchmark_strength_summary": explanation_payload["benchmark_strength_summary"],
                "explanation": explanation_payload["explanation"],
            }
        )

    frame = pd.DataFrame(rows)
    return frame.sort_values("prediction_date").reset_index(drop=True)


def _build_predictions_frame(evaluation_df: pd.DataFrame) -> pd.DataFrame:
    """Keep a compact predictions artifact for backwards-compatible inspection."""
    return evaluation_df.rename(
        columns={
            "prediction_date": "date",
            "predicted_value": "predicted",
            "actual_future_result": "actual",
        }
    )[
        [
            "date",
            "ticker",
            "actual",
            "predicted",
            "confidence_score",
            "hit_miss",
            "model_name",
            "target_name",
            "technical_state_summary",
            "news_sentiment_summary",
            "benchmark_strength_summary",
            "explanation",
        ]
    ].copy()


def _save_training_artifacts(
    ticker: str,
    period: str,
    target_name: str,
    model_name: str,
    fitted_pipeline: Pipeline,
    feature_names: list[str],
    metrics: dict[str, Any],
    predictions_df: pd.DataFrame,
    evaluation_df: pd.DataFrame,
    output_dir: str | Path | None = None,
    market: str = "US",
) -> TrainingArtifact:
    """Persist one model, its metadata, and predictions to disk."""
    base_dir = Path(output_dir or get_settings().research_models_dir)
    artifact_dir = model_security_root(base_dir, market, ticker) / period / target_name / model_name
    artifact_dir.mkdir(parents=True, exist_ok=True)

    model_path = artifact_dir / "model.pkl"
    feature_list_path = artifact_dir / "feature_list.json"
    metrics_path = artifact_dir / "metrics_summary.json"
    predictions_path = artifact_dir / "predictions.csv"
    evaluation_table_path = artifact_dir / "evaluation_table.csv"

    with model_path.open("wb") as file_handle:
        pickle.dump(fitted_pipeline, file_handle)

    feature_list_path.write_text(json.dumps(feature_names, indent=2), encoding="utf-8")
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    predictions_df.to_csv(predictions_path, index=False)
    evaluation_df.to_csv(evaluation_table_path, index=False)

    return TrainingArtifact(
        ticker=ticker,
        period=period,
        target_name=target_name,
        model_name=model_name,
        model_path=model_path,
        feature_list_path=feature_list_path,
        metrics_path=metrics_path,
        predictions_path=predictions_path,
        evaluation_table_path=evaluation_table_path,
    )


def train_baseline_model(
    dataset_df: pd.DataFrame,
    ticker: str,
    period: str,
    target_name: str,
    task_type: str,
    model_name: str,
    output_dir: str | Path | None = None,
    market: str = "US",
) -> TrainingRunResult:
    """Train one baseline model with expanding-window validation only."""
    identity = resolve_model_identity(ticker, market)
    uses_stationary_features = task_type == "regression"
    if uses_stationary_features and "close" in dataset_df.columns:
        dataset_df = prepare_stationary_feature_dataset(dataset_df)
    x_frame, y_series, date_series, source_ticker_series, feature_names = _build_feature_frame(
        dataset_df,
        target_name,
    )
    split_count = _choose_time_series_splits(len(x_frame))
    validation_gap_rows = _target_horizon_rows(target_name)
    validation_splits = _purged_date_splits(
        date_series,
        split_count=split_count,
        gap_rows=validation_gap_rows,
    )

    builder = _build_classifier_pipeline if task_type == "classification" else _build_regressor_pipeline
    base_pipeline = builder(model_name)

    evaluation_rows: list[pd.DataFrame] = []
    fold_sizes: list[dict[str, Any]] = []

    for fold_number, (train_index, test_index) in enumerate(validation_splits, start=1):
        fold_pipeline = clone(base_pipeline)

        x_train = x_frame.iloc[train_index]
        y_train = y_series.iloc[train_index]
        x_test = x_frame.iloc[test_index]
        y_test = y_series.iloc[test_index]
        fold_dates = date_series.iloc[test_index]

        training_class_count = int(y_train.nunique()) if task_type == "classification" else None
        used_single_class_fallback = task_type == "classification" and training_class_count < 2
        prediction_uncertainty_pct: float | None = None
        if used_single_class_fallback:
            # An early expanding-window fold can legitimately contain only one
            # direction.  It is still out-of-sample evidence, but a classifier
            # cannot be fitted yet.  Preserve the fold with deliberately neutral
            # confidence instead of crashing or pretending certainty.
            predictions = np.full(len(x_test), y_train.iloc[-1])
            confidence_scores = np.full(len(x_test), 0.5)
        else:
            if task_type == "regression":
                train_dates = pd.to_datetime(date_series.iloc[train_index]).dt.normalize()
                unique_train_dates = pd.Index(train_dates.unique()).sort_values()
                calibration_date_start = max(20, int(len(unique_train_dates) * 0.80))
                calibration_train_end = calibration_date_start - validation_gap_rows
                if calibration_train_end >= 30 and calibration_date_start < len(unique_train_dates):
                    inner_train_dates = unique_train_dates[:calibration_train_end]
                    calibration_dates = unique_train_dates[calibration_date_start:]
                    inner_train_mask = train_dates.isin(inner_train_dates).to_numpy()
                    calibration_mask = train_dates.isin(calibration_dates).to_numpy()
                    calibration_pipeline = clone(base_pipeline)
                    calibration_pipeline.fit(
                        x_train.iloc[inner_train_mask],
                        y_train.iloc[inner_train_mask],
                    )
                    calibration_predictions = calibration_pipeline.predict(
                        x_train.iloc[calibration_mask]
                    )
                    prediction_uncertainty_pct = _calibrate_regression_abstention_threshold(
                        y_train.iloc[calibration_mask].astype(float).to_numpy(),
                        np.asarray(calibration_predictions, dtype=float),
                    )
            fold_pipeline.fit(x_train, y_train)
            predictions = fold_pipeline.predict(x_test)
            confidence_scores = _get_prediction_confidence(
                fitted_pipeline=fold_pipeline,
                x_test=x_test,
                task_type=task_type,
                predictions=predictions,
            )

        evaluation_rows.append(
            _build_walk_forward_evaluation_frame(
                date_series=fold_dates,
                ticker=ticker,
                feature_frame=x_test,
                y_true=y_test,
                y_pred=predictions,
                confidence_scores=confidence_scores,
                model_name=model_name,
                target_name=target_name,
                task_type=task_type,
                fold_number=fold_number,
                prediction_uncertainty_pct=prediction_uncertainty_pct,
                source_ticker_series=source_ticker_series.iloc[test_index],
            )
        )
        fold_sizes.append(
            {
                "fold": fold_number,
                "train_rows": int(len(train_index)),
                "test_rows": int(len(test_index)),
                "training_class_count": training_class_count,
                "single_class_fallback": used_single_class_fallback,
                "prediction_uncertainty_pct": prediction_uncertainty_pct,
            }
        )

    if not evaluation_rows:
        raise ModelTrainingError("Time-series validation produced no prediction rows.")

    evaluation_df = pd.concat(evaluation_rows, ignore_index=True)
    predictions_df = _build_predictions_frame(evaluation_df)
    actual_series = pd.Series(evaluation_df["actual_future_result"])
    predicted_array = evaluation_df["predicted_value"].to_numpy()

    if task_type == "classification":
        metric_values = _score_classifier_predictions(actual_series, predicted_array)
    else:
        metric_values = _score_regression_predictions(actual_series.astype(float), predicted_array.astype(float))

    final_pipeline = clone(base_pipeline)
    final_pipeline.fit(x_frame, y_series)

    metrics = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "market": identity.market,
        "ticker": identity.ticker,
        "provider_symbol": identity.provider_symbol,
        "period": period,
        "target_name": target_name,
        "task_type": task_type,
        "model_name": model_name,
        "row_count": int(len(x_frame)),
        "feature_count": int(len(feature_names)),
        "time_series_splits": split_count,
        "validation_method": "purged_walk_forward_with_calibrated_abstention_and_regime_filter",
        "validation_scheme_version": VALIDATION_SCHEME_VERSION,
        "validation_gap_rows": validation_gap_rows,
        "validation_note": (
            "Random train/test shuffling is avoided because time-series evaluation must preserve time order "
            "and keep future data out of the training window. A target-horizon gap purges training labels "
            "whose future price window would overlap the test fold. Regression trade eligibility is "
            "calibrated on a trailing inner holdout, also separated by the target-horizon gap. A fixed "
            "prediction-time stress policy blocks new positions during severe selloffs, drawdowns, or volatility."
        ),
        "fold_sizes": fold_sizes,
        "metrics": metric_values,
    }
    calibrated_thresholds = [
        float(item["prediction_uncertainty_pct"])
        for item in fold_sizes
        if isinstance(item.get("prediction_uncertainty_pct"), (int, float))
        and float(item["prediction_uncertainty_pct"]) >= 0
    ]
    if calibrated_thresholds:
        metrics["prediction_abstention_threshold_pct"] = float(
            np.median(calibrated_thresholds)
        )
        metrics["prediction_abstention_calibration"] = (
            "inner_time_ordered_balanced_accuracy_and_return"
        )
    if uses_stationary_features:
        metrics["feature_schema_version"] = 2
        metrics["stationary_features"] = True

    artifact = _save_training_artifacts(
        ticker=ticker,
        period=period,
        target_name=target_name,
        model_name=model_name,
        fitted_pipeline=final_pipeline,
        feature_names=feature_names,
        metrics=metrics,
        predictions_df=predictions_df,
        evaluation_df=evaluation_df,
        output_dir=output_dir,
        market=market,
    )

    logger.info(
        "Trained baseline model ticker=%s target=%s model=%s rows=%s",
        ticker,
        target_name,
        model_name,
        len(x_frame),
    )

    return TrainingRunResult(
        ticker=ticker,
        period=period,
        target_name=target_name,
        model_name=model_name,
        task_type=task_type,
        feature_names=feature_names,
        metrics=metrics,
        predictions=predictions_df,
        evaluation_table=evaluation_df,
        artifact=artifact,
    )


def train_baseline_models_for_ticker(
    ticker: str,
    period: str = "5y",
    benchmark: str = "VOO",
    include_news_sentiment: bool = True,
    sentiment_model: str = "finbert",
    output_dir: str | Path | None = None,
    include_gradient_boosting: bool = True,
    target_names: tuple[str, ...] | list[str] | None = None,
    market: str = "US",
) -> list[TrainingRunResult]:
    """Train baseline classification and regression models for one ticker."""
    identity = resolve_security(ticker, market)
    ticker_symbol = identity.ticker
    benchmark_symbol = resolve_security(benchmark, identity.market).ticker
    dataset_df = build_feature_dataset(
        ticker=ticker_symbol,
        period=period,
        benchmark=benchmark_symbol,
        include_news_sentiment=include_news_sentiment,
        sentiment_model=sentiment_model,
        market=identity.market,
    )

    classification_models = _get_default_model_names("classification")
    regression_models = _get_default_model_names("regression")
    if not include_gradient_boosting:
        classification_models = [name for name in classification_models if name != "gradient_boosting"]
        regression_models = [name for name in regression_models if name != "gradient_boosting"]
    selected_targets = set(target_names or ("target_5d_updown", "target_5d_return"))

    run_results: list[TrainingRunResult] = []
    if "target_5d_updown" in selected_targets:
        for model_name in classification_models:
            run_results.append(
                train_baseline_model(
                    dataset_df=dataset_df,
                    ticker=ticker_symbol,
                    period=period,
                    target_name="target_5d_updown",
                    task_type="classification",
                    model_name=model_name,
                    output_dir=output_dir,
                    market=identity.market,
                )
            )

    if "target_5d_outperform" in selected_targets:
        if ticker_symbol == benchmark_symbol:
            if len(selected_targets) == 1:
                raise ModelTrainingError(
                    "A benchmark cannot train an outperformance model against itself."
                )
        else:
            stationary_dataset_df = prepare_stationary_feature_dataset(dataset_df)
            for model_name in classification_models:
                result = train_baseline_model(
                    dataset_df=stationary_dataset_df,
                    ticker=ticker_symbol,
                    period=period,
                    target_name="target_5d_outperform",
                    task_type="classification",
                    model_name=model_name,
                    output_dir=output_dir,
                    market=identity.market,
                )
                result.metrics["feature_schema_version"] = 2
                result.metrics["stationary_features"] = True
                result.metrics["benchmark_relative_target"] = True
                result.metrics["outperformance_economics_gate"] = (
                    evaluate_outperformance_economics(
                        result.evaluation_table,
                        dataset_df,
                        round_trip_cost_pct=OUTPERFORMANCE_ROUND_TRIP_COST_PCT,
                    )
                )
                result.artifact.metrics_path.write_text(
                    json.dumps(result.metrics, indent=2),
                    encoding="utf-8",
                )
                run_results.append(result)

    if "target_5d_return" in selected_targets:
        stationary_dataset_df = prepare_stationary_feature_dataset(dataset_df)
        for model_name in regression_models:
            result = train_baseline_model(
                dataset_df=stationary_dataset_df,
                ticker=ticker_symbol,
                period=period,
                target_name="target_5d_return",
                task_type="regression",
                model_name=model_name,
                output_dir=output_dir,
                market=identity.market,
            )
            result.metrics["feature_schema_version"] = 2
            result.metrics["stationary_features"] = True
            result.artifact.metrics_path.write_text(
                json.dumps(result.metrics, indent=2),
                encoding="utf-8",
            )
            run_results.append(result)

    return run_results


def train_baseline_models_for_watchlist(
    tickers: list[str],
    period: str = "5y",
    benchmark: str = "VOO",
    include_news_sentiment: bool = True,
    sentiment_model: str = "finbert",
    output_dir: str | Path | None = None,
    include_gradient_boosting: bool = True,
    target_names: tuple[str, ...] | list[str] | None = None,
) -> dict[str, list[TrainingRunResult]]:
    """Train baseline models for multiple tickers using one consistent setup."""
    results: dict[str, list[TrainingRunResult]] = {}
    failures: dict[str, str] = {}

    for ticker in tickers:
        ticker_symbol = ticker.strip().upper()
        try:
            results[ticker_symbol] = train_baseline_models_for_ticker(
                ticker=ticker_symbol,
                period=period,
                benchmark=benchmark,
                include_news_sentiment=include_news_sentiment,
                sentiment_model=sentiment_model,
                output_dir=output_dir,
                include_gradient_boosting=include_gradient_boosting,
                target_names=target_names,
            )
        except Exception as exc:  # pragma: no cover - depends on data/provider responses
            failures[ticker_symbol] = str(exc)
            logger.warning(
                "Skipping ticker during watchlist model training ticker=%s reason=%s",
                ticker_symbol,
                exc,
            )

    if not results:
        detail = "; ".join(f"{ticker}: {reason}" for ticker, reason in failures.items())
        raise ModelTrainingError(
            f"Model training failed for all requested tickers. Details: {detail or 'unknown error'}"
        )

    if failures:
        logger.warning(
            "Watchlist model training completed with partial failures succeeded=%s failed=%s",
            len(results),
            len(failures),
        )

    return results


def train_pooled_baseline_models(
    tickers: list[str],
    period: str = "5y",
    benchmark: str = "VOO",
    include_news_sentiment: bool = False,
    sentiment_model: str = "finbert",
    output_dir: str | Path | None = None,
    include_gradient_boosting: bool = True,
    target_names: tuple[str, ...] | list[str] | None = None,
    model_names: tuple[str, ...] | list[str] | None = None,
    market: str = "US",
) -> list[TrainingRunResult]:
    """Train experimental cross-ticker models with date-grouped validation."""
    benchmark_identity = resolve_security(benchmark, market)
    symbols = sorted({
        resolve_security(item, benchmark_identity.market).ticker
        for item in tickers
        if str(item).strip()
    })
    if len(symbols) < 3:
        raise ModelTrainingError("Pooled training requires at least three tickers.")

    datasets = [
        build_feature_dataset(
            ticker=symbol,
            period=period,
            benchmark=benchmark,
            include_news_sentiment=include_news_sentiment,
            sentiment_model=sentiment_model,
            market=benchmark_identity.market,
        )
        for symbol in symbols
    ]
    pooled = pd.concat(datasets, ignore_index=True).sort_values(["date", "ticker"])
    pooled = _prepare_pooled_feature_dataset(pooled)
    selected_targets = set(target_names or ("target_5d_return",))
    supported_targets = {
        "target_5d_updown",
        "target_5d_outperform",
        "target_5d_return",
    }
    unsupported_targets = selected_targets - supported_targets
    if unsupported_targets:
        raise ModelTrainingError(f"Unsupported pooled targets: {sorted(unsupported_targets)}")

    classifier_models = _get_default_model_names("classification")
    regressor_models = _get_default_model_names("regression")
    supported_models = set(classifier_models) | set(regressor_models)
    selected_model_names = list(model_names or supported_models)
    unsupported = set(selected_model_names) - supported_models
    if unsupported:
        raise ModelTrainingError(f"Unsupported pooled models: {sorted(unsupported)}")
    if not include_gradient_boosting:
        selected_model_names = [name for name in selected_model_names if name != "gradient_boosting"]

    results: list[TrainingRunResult] = []
    classification_targets = [
        target
        for target in ("target_5d_updown", "target_5d_outperform")
        if target in selected_targets
    ]
    for classification_target in classification_targets:
        selected_classifiers = [name for name in classifier_models if name in selected_model_names]
        if not selected_classifiers:
            raise ModelTrainingError("No compatible pooled classification models were selected.")
        classification_dataset = pooled
        if classification_target == "target_5d_outperform":
            classification_dataset = pooled[
                pooled["ticker"].astype(str).str.upper()
                != pooled["benchmark"].astype(str).str.upper()
            ].copy()
        for model_name in selected_classifiers:
            result = train_baseline_model(
                dataset_df=classification_dataset,
                ticker="GLOBAL",
                period=period,
                target_name=classification_target,
                task_type="classification",
                model_name=model_name,
                output_dir=output_dir,
                market=benchmark_identity.market,
            )
            result.metrics["pooled_training"] = True
            result.metrics["training_tickers"] = symbols
            result.metrics["feature_schema_version"] = 2
            result.metrics["stationary_features"] = True
            result.metrics["pooled_stationary_features"] = True
            result.artifact.metrics_path.write_text(
                json.dumps(result.metrics, indent=2),
                encoding="utf-8",
            )
            results.append(result)
    if "target_5d_return" in selected_targets:
        selected_regressors = [name for name in regressor_models if name in selected_model_names]
        if not selected_regressors:
            raise ModelTrainingError("No compatible pooled regression models were selected.")
        for model_name in selected_regressors:
            result = train_baseline_model(
                dataset_df=pooled,
                ticker="GLOBAL",
                period=period,
                target_name="target_5d_return",
                task_type="regression",
                model_name=model_name,
                output_dir=output_dir,
                market=benchmark_identity.market,
            )
            result.metrics["pooled_training"] = True
            result.metrics["training_tickers"] = symbols
            result.metrics["feature_schema_version"] = 2
            result.metrics["stationary_features"] = True
            result.metrics["pooled_stationary_features"] = True
            result.artifact.metrics_path.write_text(
                json.dumps(result.metrics, indent=2),
                encoding="utf-8",
            )
            results.append(result)
    return results
