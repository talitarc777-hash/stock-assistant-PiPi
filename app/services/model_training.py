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
from sklearn.linear_model import LinearRegression, LogisticRegression
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


def _build_feature_frame(
    dataset_df: pd.DataFrame,
    target_name: str,
) -> tuple[pd.DataFrame, pd.Series, pd.Series, list[str]]:
    """Select numeric model features and align them with one target column."""
    if target_name not in dataset_df.columns:
        raise ModelTrainingError(f"Target column not found: {target_name}")

    numeric_columns = dataset_df.select_dtypes(include=[np.number, "bool"]).columns.tolist()
    feature_columns = [
        column for column in numeric_columns
        if column not in FEATURE_EXCLUDE_COLUMNS
    ]

    if not feature_columns:
        raise ModelTrainingError("No numeric feature columns available for training.")

    training_df = dataset_df[["date", target_name] + feature_columns].copy()
    # Keep rows that have a target label and let the model pipeline's imputer handle
    # missing feature values. Dropping on *all* feature NaNs can wipe out the dataset,
    # especially when optional features (like news sentiment) are sparse.
    training_df = training_df.dropna(subset=[target_name])

    if training_df.empty:
        raise ModelTrainingError(f"No rows remain after cleaning for target {target_name}.")

    x_frame = training_df[feature_columns]
    y_series = training_df[target_name]
    date_series = pd.to_datetime(training_df["date"], errors="coerce")
    return x_frame, y_series, date_series, feature_columns


def _build_classifier_pipeline(model_name: str) -> Pipeline:
    """Return one transparent baseline classifier pipeline."""
    model_key = model_name.strip().lower()

    if model_key == "logistic_regression":
        estimator = LogisticRegression(max_iter=1000, random_state=42)
        return Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
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
                ("imputer", SimpleImputer(strategy="median")),
                ("model", estimator),
            ]
        )
    if model_key == "gradient_boosting":
        estimator = GradientBoostingClassifier(random_state=42)
        return Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
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
                ("imputer", SimpleImputer(strategy="median")),
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
                ("imputer", SimpleImputer(strategy="median")),
                ("model", estimator),
            ]
        )
    if model_key == "gradient_boosting":
        estimator = GradientBoostingRegressor(random_state=42)
        return Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                ("model", estimator),
            ]
        )

    raise ModelTrainingError(f"Unsupported regression model: {model_name}")


def _get_default_model_names(task_type: str) -> list[str]:
    """Return the baseline model list for a task type."""
    if task_type == "classification":
        return ["logistic_regression", "random_forest", "gradient_boosting"]
    if task_type == "regression":
        return ["linear_regression", "random_forest", "gradient_boosting"]
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

    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(rmse),
        "r2": float(r2_score(y_true, y_pred)),
        "direction_accuracy": direction_accuracy,
    }


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
) -> pd.DataFrame:
    """Build a chart-friendly walk-forward evaluation table.

    We keep the rows strictly time-ordered and out-of-sample only.
    Random train/test shuffling is inappropriate for time series because it mixes
    future observations into the training set and makes the evaluation unrealistically optimistic.
    """
    actual_series = pd.Series(y_true).reset_index(drop=True)
    predicted_series = pd.Series(y_pred).reset_index(drop=True)
    feature_work_df = feature_frame.reset_index(drop=True)

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
        rows.append(
            {
                "prediction_date": pd.to_datetime(date_series).dt.strftime("%Y-%m-%d").iloc[index],
                "ticker": ticker,
                "predicted_value": predicted_series.iloc[index],
                "confidence_score": confidence_score,
                "actual_future_result": actual_series.iloc[index],
                "hit_miss": hit_miss.iloc[index],
                "model_name": model_name,
                "target_name": target_name,
                "task_type": task_type,
                "evaluation_window": fold_number,
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
) -> TrainingArtifact:
    """Persist one model, its metadata, and predictions to disk."""
    base_dir = Path(output_dir or get_settings().research_models_dir)
    artifact_dir = base_dir / ticker / period / target_name / model_name
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
) -> TrainingRunResult:
    """Train one baseline model with expanding-window validation only."""
    x_frame, y_series, date_series, feature_names = _build_feature_frame(dataset_df, target_name)
    split_count = _choose_time_series_splits(len(x_frame))
    splitter = TimeSeriesSplit(n_splits=split_count)

    builder = _build_classifier_pipeline if task_type == "classification" else _build_regressor_pipeline
    base_pipeline = builder(model_name)

    evaluation_rows: list[pd.DataFrame] = []
    fold_sizes: list[dict[str, int]] = []

    for fold_number, (train_index, test_index) in enumerate(splitter.split(x_frame), start=1):
        fold_pipeline = clone(base_pipeline)

        x_train = x_frame.iloc[train_index]
        y_train = y_series.iloc[train_index]
        x_test = x_frame.iloc[test_index]
        y_test = y_series.iloc[test_index]
        fold_dates = date_series.iloc[test_index]

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
            )
        )
        fold_sizes.append(
            {
                "fold": fold_number,
                "train_rows": int(len(train_index)),
                "test_rows": int(len(test_index)),
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
        "ticker": ticker,
        "period": period,
        "target_name": target_name,
        "task_type": task_type,
        "model_name": model_name,
        "row_count": int(len(x_frame)),
        "feature_count": int(len(feature_names)),
        "time_series_splits": split_count,
        "validation_method": "walk_forward_expanding_window",
        "validation_note": (
            "Random train/test shuffling is avoided because time-series evaluation must preserve time order "
            "and keep future data out of the training window."
        ),
        "fold_sizes": fold_sizes,
        "metrics": metric_values,
    }

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
) -> list[TrainingRunResult]:
    """Train baseline classification and regression models for one ticker."""
    ticker_symbol = ticker.strip().upper()
    dataset_df = build_feature_dataset(
        ticker=ticker_symbol,
        period=period,
        benchmark=benchmark,
        include_news_sentiment=include_news_sentiment,
        sentiment_model=sentiment_model,
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
                )
            )

    if "target_5d_return" in selected_targets:
        for model_name in regression_models:
            run_results.append(
                train_baseline_model(
                    dataset_df=dataset_df,
                    ticker=ticker_symbol,
                    period=period,
                    target_name="target_5d_return",
                    task_type="regression",
                    model_name=model_name,
                    output_dir=output_dir,
                )
            )

    return run_results


def train_baseline_models_for_watchlist(
    tickers: list[str],
    period: str = "5y",
    benchmark: str = "VOO",
    include_news_sentiment: bool = True,
    sentiment_model: str = "finbert",
    output_dir: str | Path | None = None,
    include_gradient_boosting: bool = True,
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
