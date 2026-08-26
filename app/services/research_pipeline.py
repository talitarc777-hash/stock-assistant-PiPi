"""Research dataset builder for stock-model experimentation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from app.core.settings import get_settings
from app.services.indicators import add_technical_indicators
from app.services.market_data import get_price_history
from app.services.market_config import model_security_root, resolve_security
from app.services.news_sentiment import build_daily_news_features

logger = logging.getLogger(__name__)

OUTPERFORMANCE_ROUND_TRIP_COST_PCT = 0.10


@dataclass(frozen=True)
class ResearchDatasetArtifact:
    """Paths returned after a dataset is written to disk."""

    ticker: str
    benchmark: str
    period: str
    dataset_path: Path
    metadata_path: Path
    row_count: int


def _return_price_series(df: pd.DataFrame) -> pd.Series:
    """Use adjusted close for returns, with a row-safe raw-close fallback.

    Trading and portfolio valuation still use the executable raw close.  Model
    return labels use adjusted close so cash distributions do not appear as
    unexplained losses.  A missing or non-positive adjusted value is never
    fabricated; that row falls back to the provider's raw close.
    """
    close = pd.to_numeric(df["close"], errors="coerce")
    if "adj_close" not in df.columns:
        return close
    adjusted = pd.to_numeric(df["adj_close"], errors="coerce")
    return adjusted.where(adjusted.notna() & (adjusted > 0), close)


def _add_return_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add percentage return features over multiple lookback windows."""
    result = df.copy()
    return_price = _return_price_series(result)
    result["return_1d_pct"] = return_price.pct_change(periods=1) * 100
    result["return_5d_pct"] = return_price.pct_change(periods=5) * 100
    result["return_20d_pct"] = return_price.pct_change(periods=20) * 100
    result["return_1m_pct"] = return_price.pct_change(periods=21) * 100
    result["return_3m_pct"] = return_price.pct_change(periods=63) * 100
    result["return_6m_pct"] = return_price.pct_change(periods=126) * 100
    result["return_12m_pct"] = return_price.pct_change(periods=252) * 100
    return result


def _build_benchmark_feature_frame(benchmark_df: pd.DataFrame) -> pd.DataFrame:
    """Build date-aligned benchmark return features."""
    benchmark_features = benchmark_df[["date"]].copy()
    return_price = _return_price_series(benchmark_df).reset_index(drop=True)
    benchmark_features["benchmark_return_1d_pct"] = return_price.pct_change(1) * 100
    benchmark_features["benchmark_return_5d_pct"] = return_price.pct_change(5) * 100
    benchmark_features["benchmark_return_20d_pct"] = return_price.pct_change(20) * 100
    benchmark_features["benchmark_return_1m_pct"] = return_price.pct_change(21) * 100
    benchmark_features["benchmark_return_3m_pct"] = return_price.pct_change(63) * 100
    benchmark_features["benchmark_return_6m_pct"] = return_price.pct_change(126) * 100
    benchmark_features["benchmark_return_12m_pct"] = return_price.pct_change(252) * 100
    return benchmark_features


def _add_benchmark_relative_features(
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
) -> pd.DataFrame:
    """Merge benchmark returns and compute excess-return features."""
    result = df.copy()
    benchmark_features = _build_benchmark_feature_frame(benchmark_df)
    result = result.merge(benchmark_features, on="date", how="left")

    for period_label in ("1d", "5d", "20d", "1m", "3m", "6m", "12m"):
        ticker_col = f"return_{period_label}_pct"
        benchmark_col = f"benchmark_return_{period_label}_pct"
        result[f"excess_return_{period_label}_pct"] = result[ticker_col] - result[benchmark_col]

    excess_score = np.zeros(len(result), dtype=float)
    for period_label in ("1m", "3m", "6m", "12m"):
        excess_score += (result[f"excess_return_{period_label}_pct"] > 0).fillna(False).astype(int) * 25
    result["benchmark_strength_score"] = excess_score.astype(int)

    return result


def _add_target_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add future-looking target labels for later model training."""
    result = df.copy()

    return_price = _return_price_series(result)
    future_close_5d = return_price.shift(-5)
    future_close_20d = return_price.shift(-20)

    result["target_5d_return"] = ((future_close_5d / return_price) - 1) * 100
    future_benchmark_return_5d = result["benchmark_return_5d_pct"].shift(-5)
    result["target_5d_excess_return"] = (
        result["target_5d_return"] - future_benchmark_return_5d
    )

    target_updown = np.where(
        result["target_5d_return"].isna(),
        pd.NA,
        np.where(result["target_5d_return"] > 0, 1, 0),
    )
    result["target_5d_updown"] = pd.Series(target_updown, index=result.index, dtype="Int64")

    target_outperform = np.where(
        result["target_5d_excess_return"].isna(),
        pd.NA,
        np.where(
            result["target_5d_excess_return"] > OUTPERFORMANCE_ROUND_TRIP_COST_PCT,
            1,
            0,
        ),
    )
    result["target_5d_outperform"] = pd.Series(
        target_outperform,
        index=result.index,
        dtype="Int64",
    )

    future_20d_return = ((future_close_20d / return_price) - 1) * 100
    regime = np.where(
        future_20d_return.isna(),
        pd.NA,
        np.where(
            future_20d_return >= 2.0,
            "bullish",
            np.where(future_20d_return <= -2.0, "bearish", "neutral"),
        ),
    )
    result["target_20d_regime"] = pd.Series(regime, index=result.index, dtype="string")

    return result


def build_feature_dataset(
    ticker: str,
    period: str = "5y",
    benchmark: str = "VOO",
    include_news_sentiment: bool = True,
    sentiment_model: str = "finbert",
    market: str = "US",
) -> pd.DataFrame:
    """
    Build one daily feature dataset for a single ticker.

    The dataset combines:
    - raw OHLCV history
    - return features
    - technical indicators
    - benchmark-relative features versus VOO (or another benchmark)
    - optional lightweight Yahoo-news sentiment features
    - 5-day and 20-day prediction targets
    """
    ticker_identity = resolve_security(ticker, market)
    benchmark_identity = resolve_security(benchmark, market)
    ticker_symbol = ticker_identity.ticker
    benchmark_symbol = benchmark_identity.ticker

    logger.info(
        "Building research dataset ticker=%s period=%s benchmark=%s",
        ticker_symbol,
        period,
        benchmark_symbol,
    )

    price_df = get_price_history(ticker_symbol, period=period, market=ticker_identity.market)
    benchmark_df = price_df.copy() if ticker_symbol == benchmark_symbol else get_price_history(
        benchmark_symbol,
        period=period,
        market=benchmark_identity.market,
    )

    dataset_df = _add_return_features(price_df)
    dataset_df = add_technical_indicators(dataset_df)
    dataset_df = _add_benchmark_relative_features(dataset_df, benchmark_df)

    if include_news_sentiment:
        news_df = build_daily_news_features(
            ticker=ticker_identity.provider_symbol,
            date_index=dataset_df["date"],
            sentiment_model=sentiment_model,
            fallback_to_lexicon=True,
        )
        dataset_df = dataset_df.merge(news_df, on="date", how="left", suffixes=("", "_news"))

    dataset_df = _add_target_columns(dataset_df)
    dataset_df["ticker"] = ticker_symbol
    dataset_df["benchmark"] = benchmark_symbol
    dataset_df["market"] = ticker_identity.market

    ordered_columns = [
        "date",
        "ticker",
        "benchmark",
        "market",
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "volume",
    ]
    remaining_columns = [column for column in dataset_df.columns if column not in ordered_columns]
    dataset_df = dataset_df[ordered_columns + remaining_columns]

    return dataset_df.sort_values("date").reset_index(drop=True)


def save_feature_dataset(
    dataset_df: pd.DataFrame,
    ticker: str,
    period: str,
    benchmark: str = "VOO",
    output_dir: str | Path | None = None,
    market: str = "US",
) -> ResearchDatasetArtifact:
    """
    Save one dataset under a beginner-friendly local folder structure.

    Output structure:
    - data/research/<ticker>/<period>/features.csv
    - data/research/<ticker>/<period>/metadata.json
    """
    base_dir = Path(output_dir or get_settings().research_data_dir)
    identity = resolve_security(ticker, market)
    ticker_dir = model_security_root(base_dir, identity.market, identity.ticker) / period
    ticker_dir.mkdir(parents=True, exist_ok=True)

    dataset_path = ticker_dir / "features.csv"
    metadata_path = ticker_dir / "metadata.json"

    dataset_df.to_csv(dataset_path, index=False)

    metadata = {
        "ticker": identity.ticker,
        "market": identity.market,
        "provider_symbol": identity.provider_symbol,
        "benchmark": benchmark.strip().upper(),
        "period": period,
        "row_count": int(len(dataset_df)),
        "column_count": int(len(dataset_df.columns)),
        "start_date": str(dataset_df["date"].min()) if not dataset_df.empty else None,
        "end_date": str(dataset_df["date"].max()) if not dataset_df.empty else None,
        "columns": list(dataset_df.columns),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    logger.info("Saved research dataset to %s", dataset_path)

    return ResearchDatasetArtifact(
        ticker=ticker.strip().upper(),
        benchmark=benchmark.strip().upper(),
        period=period,
        dataset_path=dataset_path,
        metadata_path=metadata_path,
        row_count=len(dataset_df),
    )


def build_and_save_feature_dataset(
    ticker: str,
    period: str = "5y",
    benchmark: str = "VOO",
    output_dir: str | Path | None = None,
    include_news_sentiment: bool = True,
    sentiment_model: str = "finbert",
    market: str = "US",
) -> ResearchDatasetArtifact:
    """Convenience wrapper to build a dataset and save it locally."""
    dataset_df = build_feature_dataset(
        ticker=ticker,
        period=period,
        benchmark=benchmark,
        include_news_sentiment=include_news_sentiment,
        sentiment_model=sentiment_model,
        market=market,
    )
    return save_feature_dataset(
        dataset_df=dataset_df,
        ticker=ticker,
        period=period,
        benchmark=benchmark,
        output_dir=output_dir,
        market=market,
    )


def build_feature_datasets_for_tickers(
    tickers: list[str],
    period: str = "5y",
    benchmark: str = "VOO",
    output_dir: str | Path | None = None,
    include_news_sentiment: bool = True,
    sentiment_model: str = "finbert",
) -> list[ResearchDatasetArtifact]:
    """Build and save research datasets for multiple tickers."""
    artifacts: list[ResearchDatasetArtifact] = []

    for ticker in tickers:
        artifacts.append(
            build_and_save_feature_dataset(
                ticker=ticker,
                period=period,
                benchmark=benchmark,
                output_dir=output_dir,
                include_news_sentiment=include_news_sentiment,
                sentiment_model=sentiment_model,
            )
        )

    return artifacts
