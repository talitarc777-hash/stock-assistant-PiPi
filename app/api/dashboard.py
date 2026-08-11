"""Chart-ready and dashboard summary endpoints."""

from __future__ import annotations

import logging
import math
from typing import Literal

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.api_utils import (
    PERIOD_PATTERN,
    TICKER_PATTERN,
    parse_ticker_csv,
    to_json_safe,
    to_json_safe_dict,
)
from app.services.indicators import IndicatorInputError, add_technical_indicators
from app.services.market_data import (
    EmptyDataError,
    InvalidTickerError,
    MarketDataError,
    get_price_history,
)
from app.services.market_config import resolve_security
from app.services.scoring import ScoringInputError, score_from_indicators

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


def _downsample_df(df: pd.DataFrame, max_points: int = 750) -> pd.DataFrame:
    """
    Downsample DataFrame rows to keep payloads reasonable.

    Keeps the time order and always includes the most recent row.
    """
    if len(df) <= max_points:
        return df

    step = max(1, math.ceil(len(df) / max_points))
    sampled = df.iloc[::step].copy()
    if sampled.iloc[-1]["date"] != df.iloc[-1]["date"]:
        sampled = pd.concat([sampled, df.iloc[[-1]]], ignore_index=True)
    return sampled.reset_index(drop=True)


def _compute_score_series_over_time(indicators_df: pd.DataFrame) -> list[dict[str, Any]]:
    """Compute score over time from indicator history."""
    timeline: list[dict[str, Any]] = []

    for end_index in range(1, len(indicators_df) + 1):
        slice_df = indicators_df.iloc[:end_index]
        score = score_from_indicators(slice_df)
        row = slice_df.iloc[-1]
        timeline.append(
            {
                "date": row["date"].strftime("%Y-%m-%d"),
                "total_score": score.total_score,
                "label": score.label,
            }
        )
    return timeline


class ChartSeriesPoint(BaseModel):
    """Chart point for OHLCV + indicators."""

    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    adj_close: float | None
    volume: float | None
    sma_20: float | None
    sma_50: float | None
    sma_200: float | None
    rsi_14: float | None
    macd_line: float | None
    macd_signal: float | None


class ScoreSeriesPoint(BaseModel):
    """Score-over-time chart point."""

    date: str
    total_score: int
    label: str


class ChartDataResponse(BaseModel):
    """Response model for /chart-data."""

    ticker: str
    market: str = "US"
    provider_symbol: str | None = None
    currency: str = "USD"
    currency_symbol: str = "$"
    period: str
    points: int
    series: list[ChartSeriesPoint]
    score_series: list[ScoreSeriesPoint]


class SummaryItemResponse(BaseModel):
    """Per-ticker dashboard summary."""

    ticker: str
    latest_close: float
    daily_percent_change: float | None
    score: int
    label: str
    action_summary: str
    above_sma200: bool
    rsi: float | None
    macd_bullish: bool


class FailedTickerResponse(BaseModel):
    """Per-ticker failure details for dashboard summary requests."""

    ticker: str
    error: str


class SummaryDashboardResponse(BaseModel):
    """Response model for /summary-dashboard."""

    tickers: list[SummaryItemResponse]
    failed_tickers: list[FailedTickerResponse]


@router.get("/chart-data", response_model=ChartDataResponse)
def chart_data(
    ticker: str = Query(
        ...,
        min_length=1,
        max_length=15,
        pattern=TICKER_PATTERN,
        description="Ticker symbol, e.g. VOO",
    ),
    period: str = Query(
        "5y",
        pattern=PERIOD_PATTERN,
        description="History period, e.g. 1y, 5y, max",
    ),
    market: Literal["US", "HK"] = "US",
) -> ChartDataResponse:
    """
    Return chart-ready OHLCV + indicator series with ISO dates.

    Includes:
    - OHLCV
    - SMA20/SMA50/SMA200
    - RSI14
    - MACD line and signal
    - total score over time
    """
    logger.info("Request /chart-data ticker=%s period=%s", ticker, period)
    try:
        price_df = get_price_history(ticker=ticker, period=period, market=market)
        indicators_df = add_technical_indicators(price_df)
    except InvalidTickerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmptyDataError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (MarketDataError, IndicatorInputError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected error in /chart-data")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc

    chart_df = indicators_df[
        [
            "date",
            "open",
            "high",
            "low",
            "close",
            "adj_close",
            "volume",
            "sma_20",
            "sma_50",
            "sma_200",
            "rsi_14",
            "macd_line",
            "macd_signal",
        ]
    ].copy()

    chart_df = _downsample_df(chart_df, max_points=750)
    chart_df["date"] = chart_df["date"].dt.strftime("%Y-%m-%d")

    score_timeline = _compute_score_series_over_time(indicators_df)
    # Match score series density to the chart series to keep response compact.
    score_df = pd.DataFrame(score_timeline)
    score_df["date"] = pd.to_datetime(score_df["date"], errors="coerce")
    score_df = score_df.dropna(subset=["date"])
    score_df = _downsample_df(score_df, max_points=750)
    score_df["date"] = score_df["date"].dt.strftime("%Y-%m-%d")

    series = [ChartSeriesPoint(**to_json_safe_dict(row)) for row in chart_df.to_dict(orient="records")]
    score_series = [
        ScoreSeriesPoint(**to_json_safe_dict(row)) for row in score_df.to_dict(orient="records")
    ]

    identity = resolve_security(ticker, market)
    return ChartDataResponse(
        ticker=identity.ticker,
        market=identity.market,
        provider_symbol=identity.provider_symbol,
        currency=identity.currency,
        currency_symbol=identity.currency_symbol,
        period=period,
        points=len(series),
        series=series,
        score_series=score_series,
    )


@router.get("/summary-dashboard", response_model=SummaryDashboardResponse)
def summary_dashboard(
    tickers: str = Query(..., description="Comma-separated tickers, e.g. VOO,SPY,QQQ"),
    period: str = Query(
        "5y",
        pattern=PERIOD_PATTERN,
        description="History period, e.g. 1y, 5y, max",
    ),
) -> SummaryDashboardResponse:
    """Return compact per-ticker summary fields for dashboard cards/tables."""
    logger.info("Request /summary-dashboard tickers=%s period=%s", tickers, period)
    try:
        ticker_list = parse_ticker_csv(tickers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    items: list[SummaryItemResponse] = []
    failed_tickers: list[FailedTickerResponse] = []

    for ticker in ticker_list:
        try:
            price_df = get_price_history(ticker=ticker, period=period)
            indicators_df = add_technical_indicators(price_df)
            score = score_from_indicators(indicators_df)

            latest = indicators_df.iloc[-1]
            prev_close = float(indicators_df.iloc[-2]["close"]) if len(indicators_df) >= 2 else None
            latest_close = float(latest["close"])
            daily_pct_change = (
                ((latest_close - prev_close) / prev_close) * 100
                if (prev_close is not None and prev_close != 0)
                else None
            )
            above_sma200 = (
                bool(latest_close > float(latest["sma_200"])) if pd.notna(latest["sma_200"]) else False
            )
            rsi_value = float(latest["rsi_14"]) if pd.notna(latest["rsi_14"]) else None
            macd_bullish = (
                bool(float(latest["macd_line"]) > float(latest["macd_signal"]))
                if (pd.notna(latest["macd_line"]) and pd.notna(latest["macd_signal"]))
                else False
            )

            items.append(
                SummaryItemResponse(
                    ticker=ticker,
                    latest_close=float(to_json_safe(latest_close)),
                    daily_percent_change=to_json_safe(daily_pct_change),
                    score=score.total_score,
                    label=score.label,
                    action_summary=score.action_summary,
                    above_sma200=above_sma200,
                    rsi=to_json_safe(rsi_value),
                    macd_bullish=macd_bullish,
                )
            )
        except (InvalidTickerError, EmptyDataError, MarketDataError, IndicatorInputError, ScoringInputError) as exc:
            logger.warning("Summary failed for ticker=%s: %s", ticker, exc)
            failed_tickers.append(FailedTickerResponse(ticker=ticker, error=str(exc)))
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("Unexpected summary failure for ticker=%s", ticker)
            failed_tickers.append(
                FailedTickerResponse(ticker=ticker, error="Unexpected server error.")
            )

    return SummaryDashboardResponse(tickers=items, failed_tickers=failed_tickers)
