"""Tests for baseline model training helpers."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np
import pandas as pd

try:
    from app.services.model_training import (
        ModelTrainingError,
        POOLED_LEVEL_FEATURES,
        _build_classifier_pipeline,
        _build_regressor_pipeline,
        _choose_time_series_splits,
        _purged_date_splits,
        _prepare_pooled_feature_dataset,
        train_baseline_model,
        train_baseline_models_for_ticker,
        train_pooled_baseline_models,
    )
    SKLEARN_AVAILABLE = True
except ModuleNotFoundError:
    ModelTrainingError = ValueError  # type: ignore[assignment]
    _choose_time_series_splits = None
    train_baseline_model = None
    SKLEARN_AVAILABLE = False


def _build_synthetic_dataset(row_count: int = 100) -> pd.DataFrame:
    """Create a deterministic numeric dataset for model-training tests."""
    dates = pd.date_range("2024-01-01", periods=row_count, freq="B")
    base = np.linspace(100.0, 140.0, row_count)
    wave = np.sin(np.linspace(0, 6, row_count))
    close = base + wave

    frame = pd.DataFrame(
        {
            "date": dates,
            "ticker": ["TEST"] * row_count,
            "benchmark": ["VOO"] * row_count,
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "adj_close": close,
            "volume": np.linspace(1_000_000, 1_500_000, row_count),
            "return_1d_pct": pd.Series(close).pct_change().fillna(0.0) * 100,
            "sma_20": pd.Series(close).rolling(20, min_periods=1).mean(),
            "sma_50": pd.Series(close).rolling(50, min_periods=1).mean(),
            "sma_200": pd.Series(close).rolling(60, min_periods=1).mean(),
            "ema_12": pd.Series(close).ewm(span=12, adjust=False).mean(),
            "ema_26": pd.Series(close).ewm(span=26, adjust=False).mean(),
            "rsi_14": np.linspace(45.0, 65.0, row_count),
            "macd_line": np.linspace(-1.0, 1.0, row_count),
            "macd_signal": np.linspace(-1.2, 0.8, row_count),
            "macd_histogram": np.linspace(0.2, 0.2, row_count),
            "avg_volume_20": pd.Series(np.linspace(1_000_000, 1_500_000, row_count)).rolling(20, min_periods=1).mean(),
            "rolling_volatility_20_pct": np.linspace(10.0, 18.0, row_count),
            "distance_from_52w_high_pct": np.linspace(-8.0, -1.0, row_count),
            "benchmark_strength_score": np.where(np.arange(row_count) % 2 == 0, 75, 50),
            "article_count": np.where(np.arange(row_count) % 3 == 0, 2, 0),
            "average_sentiment": np.where(np.arange(row_count) % 4 == 0, 0.2, -0.1),
            "positive_article_ratio": np.where(np.arange(row_count) % 4 == 0, 1.0, 0.0),
            "negative_article_ratio": np.where(np.arange(row_count) % 5 == 0, 0.5, 0.0),
        }
    )

    future_return = ((pd.Series(close).shift(-5) / pd.Series(close)) - 1) * 100
    frame["target_5d_return"] = future_return
    frame["target_5d_updown"] = (future_return > 0).astype("Int64")
    frame["target_20d_regime"] = "neutral"
    return frame


@contextmanager
def _workspace_temporary_directory():
    """Keep artifact tests inside the writable workspace on sandboxed Windows."""
    root = Path("data") / "test_model_training"
    root.mkdir(parents=True, exist_ok=True)
    output_dir = root / uuid4().hex
    output_dir.mkdir()
    try:
        yield str(output_dir)
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


@unittest.skipUnless(SKLEARN_AVAILABLE, "scikit-learn is required for model-training tests")
class ModelTrainingTests(unittest.TestCase):
    def test_model_pipelines_keep_features_that_are_empty_in_early_folds(self) -> None:
        features = pd.DataFrame(
            {
                "available": [1.0, 2.0, 3.0, 4.0],
                "not_yet_available": [np.nan, np.nan, np.nan, np.nan],
            }
        )

        for model_name in ("logistic_regression", "random_forest", "gradient_boosting"):
            pipeline = _build_classifier_pipeline(model_name)
            transformed = pipeline.named_steps["imputer"].fit_transform(features)
            self.assertEqual(transformed.shape[1], features.shape[1])

        for model_name in ("linear_regression", "random_forest", "gradient_boosting"):
            pipeline = _build_regressor_pipeline(model_name)
            transformed = pipeline.named_steps["imputer"].fit_transform(features)
            self.assertEqual(transformed.shape[1], features.shape[1])

    """Verify core baseline-model training behavior."""

    def test_choose_time_series_splits_rejects_small_datasets(self) -> None:
        with self.assertRaises(ModelTrainingError):
            _choose_time_series_splits(20)

    def test_purged_date_splits_keep_every_ticker_on_one_side(self) -> None:
        dates = pd.Series(pd.date_range("2024-01-01", periods=80, freq="B").repeat(3))
        splits = _purged_date_splits(dates, split_count=3, gap_rows=5)

        self.assertEqual(len(splits), 3)
        for train_rows, test_rows in splits:
            train_dates = set(pd.to_datetime(dates.iloc[train_rows]).dt.date)
            test_dates = set(pd.to_datetime(dates.iloc[test_rows]).dt.date)
            self.assertTrue(train_dates.isdisjoint(test_dates))
            self.assertEqual(len(test_rows) % 3, 0)

    def test_pooled_features_are_invariant_to_price_and_volume_scale(self) -> None:
        base = _build_synthetic_dataset(100)
        scaled = base.copy()
        for column in (
            "open", "high", "low", "close", "adj_close", "sma_20", "sma_50",
            "sma_200", "ema_12", "ema_26", "macd_line", "macd_signal",
            "macd_histogram",
        ):
            scaled[column] *= 3.0
        for column in ("volume", "avg_volume_20"):
            scaled[column] *= 7.0

        first = _prepare_pooled_feature_dataset(base)
        second = _prepare_pooled_feature_dataset(scaled)
        normalized = [
            "overnight_gap_pct", "intraday_range_pct", "close_location_in_range",
            "close_vs_sma_20_pct", "close_vs_sma_50_pct", "close_vs_sma_200_pct",
            "close_vs_ema_12_pct", "close_vs_ema_26_pct", "macd_line_pct",
            "macd_signal_pct", "macd_histogram_pct", "volume_vs_20d_avg",
        ]
        np.testing.assert_allclose(
            first[normalized].to_numpy(),
            second[normalized].to_numpy(),
            equal_nan=True,
            rtol=1e-10,
            atol=1e-10,
        )
        self.assertTrue(POOLED_LEVEL_FEATURES.isdisjoint(first.columns))

    def test_train_baseline_model_saves_classification_artifacts(self) -> None:
        dataset_df = _build_synthetic_dataset()

        with _workspace_temporary_directory() as temp_dir:
            result = train_baseline_model(
                dataset_df=dataset_df,
                ticker="TEST",
                period="2y",
                target_name="target_5d_updown",
                task_type="classification",
                model_name="logistic_regression",
                output_dir=temp_dir,
            )

            self.assertEqual(result.task_type, "classification")
            self.assertGreater(result.metrics["row_count"], 0)
            self.assertIn("accuracy", result.metrics["metrics"])
            self.assertEqual(
                result.metrics["validation_method"],
                "purged_walk_forward_with_calibrated_abstention_and_regime_filter",
            )
            self.assertEqual(result.metrics["validation_gap_rows"], 5)
            self.assertEqual(result.metrics["validation_scheme_version"], 4)
            self.assertIn("precision", result.metrics["metrics"])
            self.assertIn("recall", result.metrics["metrics"])
            self.assertFalse(result.predictions.empty)
            self.assertFalse(result.evaluation_table.empty)
            self.assertIn("confidence_score", result.evaluation_table.columns)
            self.assertIn("hit_miss", result.evaluation_table.columns)
            self.assertIn("technical_state_summary", result.evaluation_table.columns)
            self.assertIn("news_sentiment_summary", result.evaluation_table.columns)
            self.assertIn("benchmark_strength_summary", result.evaluation_table.columns)
            self.assertIn("explanation", result.evaluation_table.columns)
            self.assertTrue(
                any(fold["single_class_fallback"] for fold in result.metrics["fold_sizes"])
            )
            fallback_rows = result.evaluation_table[result.evaluation_table["evaluation_window"] == 1]
            self.assertTrue((fallback_rows["confidence_score"] == 0.5).all())
            self.assertTrue(Path(result.artifact.model_path).exists())
            self.assertTrue(Path(result.artifact.feature_list_path).exists())
            self.assertTrue(Path(result.artifact.metrics_path).exists())
            self.assertTrue(Path(result.artifact.predictions_path).exists())
            self.assertTrue(Path(result.artifact.evaluation_table_path).exists())

    def test_train_baseline_model_saves_regression_artifacts(self) -> None:
        dataset_df = _build_synthetic_dataset()

        with _workspace_temporary_directory() as temp_dir:
            result = train_baseline_model(
                dataset_df=dataset_df,
                ticker="TEST",
                period="2y",
                target_name="target_5d_return",
                task_type="regression",
                model_name="linear_regression",
                output_dir=temp_dir,
            )

            self.assertEqual(result.task_type, "regression")
            self.assertIn("rmse", result.metrics["metrics"])
            self.assertIn("absolute_error_80_pct", result.metrics["metrics"])
            self.assertIn("absolute_error_95_pct", result.metrics["metrics"])
            self.assertTrue(result.metrics.get("stationary_features"))
            self.assertEqual(result.metrics.get("feature_schema_version"), 2)
            self.assertFalse(result.predictions.empty)
            self.assertFalse(result.evaluation_table.empty)
            self.assertIn("actual_future_result", result.evaluation_table.columns)
            self.assertIn("prediction_uncertainty_pct", result.evaluation_table.columns)
            self.assertIn("is_actionable_signal", result.evaluation_table.columns)
            self.assertIn("is_regime_trade_allowed", result.evaluation_table.columns)
            self.assertIn("market_regime_level", result.evaluation_table.columns)
            self.assertTrue(
                any(
                    fold["prediction_uncertainty_pct"] is not None
                    for fold in result.metrics["fold_sizes"]
                )
            )
            self.assertIn("hit_miss", result.evaluation_table.columns)
            self.assertIn("explanation", result.evaluation_table.columns)

    def test_pooled_training_records_tickers_and_source_rows(self) -> None:
        def fake_dataset(ticker: str, **_kwargs) -> pd.DataFrame:
            frame = _build_synthetic_dataset(100)
            frame["ticker"] = ticker
            return frame

        with _workspace_temporary_directory() as temp_dir, patch(
            "app.services.model_training.build_feature_dataset",
            side_effect=fake_dataset,
        ):
            results = train_pooled_baseline_models(
                ["MSFT", "AAPL", "VOO"],
                output_dir=temp_dir,
                include_gradient_boosting=False,
            )

        self.assertEqual(len(results), 2)
        for result in results:
            self.assertEqual(result.ticker, "GLOBAL")
            self.assertTrue(result.metrics["pooled_training"])
            self.assertEqual(result.metrics["training_tickers"], ["AAPL", "MSFT", "VOO"])
            self.assertEqual(
                set(result.evaluation_table["source_ticker"]),
                {"AAPL", "MSFT", "VOO"},
            )

    def test_pooled_training_supports_stationary_direction_classifier(self) -> None:
        def fake_dataset(ticker: str, **_kwargs) -> pd.DataFrame:
            frame = _build_synthetic_dataset(100)
            frame["ticker"] = ticker
            return frame

        with _workspace_temporary_directory() as temp_dir, patch(
            "app.services.model_training.build_feature_dataset",
            side_effect=fake_dataset,
        ):
            results = train_pooled_baseline_models(
                ["MSFT", "AAPL", "VOO"],
                output_dir=temp_dir,
                target_names=("target_5d_updown",),
                model_names=("logistic_regression",),
            )

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.task_type, "classification")
        self.assertEqual(result.target_name, "target_5d_updown")
        self.assertTrue(result.metrics["pooled_training"])
        self.assertTrue(result.metrics["stationary_features"])
        self.assertEqual(result.metrics["feature_schema_version"], 2)
        self.assertEqual(
            set(result.evaluation_table["source_ticker"]),
            {"AAPL", "MSFT", "VOO"},
        )

    def test_pooled_outperformance_target_excludes_benchmark_and_target_leakage(self) -> None:
        def fake_dataset(ticker: str, **_kwargs) -> pd.DataFrame:
            frame = _build_synthetic_dataset(100)
            frame["ticker"] = ticker
            frame["benchmark"] = "VOO"
            frame["target_5d_excess_return"] = frame["target_5d_return"] - 0.2
            frame["target_5d_outperform"] = (
                frame["target_5d_excess_return"] > 0.1
            ).astype("Int64")
            return frame

        with _workspace_temporary_directory() as temp_dir, patch(
            "app.services.model_training.build_feature_dataset",
            side_effect=fake_dataset,
        ):
            results = train_pooled_baseline_models(
                ["MSFT", "AAPL", "VOO"],
                output_dir=temp_dir,
                target_names=("target_5d_outperform",),
                model_names=("logistic_regression",),
            )

        result = results[0]
        self.assertEqual(result.target_name, "target_5d_outperform")
        self.assertNotIn("target_5d_return", result.feature_names)
        self.assertNotIn("target_5d_excess_return", result.feature_names)
        self.assertNotIn("target_5d_outperform", result.feature_names)
        self.assertEqual(
            set(result.evaluation_table["source_ticker"]),
            {"AAPL", "MSFT"},
        )

    @patch("app.services.model_training.build_feature_dataset")
    def test_individual_outperformance_training_is_stationary_and_rejects_self_benchmark(
        self,
        mock_dataset,
    ) -> None:
        frame = _build_synthetic_dataset(100)
        frame["target_5d_excess_return"] = frame["target_5d_return"] - 0.2
        frame["target_5d_outperform"] = (
            frame["target_5d_excess_return"] > 0.1
        ).astype("Int64")
        mock_dataset.return_value = frame

        with _workspace_temporary_directory() as temp_dir:
            results = train_baseline_models_for_ticker(
                "AAPL",
                benchmark="VOO",
                output_dir=temp_dir,
                include_gradient_boosting=False,
                target_names=("target_5d_outperform",),
            )
            with self.assertRaisesRegex(ModelTrainingError, "cannot train"):
                train_baseline_models_for_ticker(
                    "VOO",
                    benchmark="VOO",
                    output_dir=temp_dir,
                    include_gradient_boosting=False,
                    target_names=("target_5d_outperform",),
                )

        self.assertEqual(len(results), 2)
        for result in results:
            self.assertEqual(result.target_name, "target_5d_outperform")
            self.assertTrue(result.metrics["stationary_features"])
            self.assertTrue(result.metrics["benchmark_relative_target"])
            self.assertIn("passed", result.metrics["outperformance_economics_gate"])
            self.assertFalse(any(name.startswith("target_") for name in result.feature_names))



if __name__ == "__main__":
    unittest.main()
