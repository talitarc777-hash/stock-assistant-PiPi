"""API routes for market data endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.api_utils import PERIOD_PATTERN, TICKER_PATTERN, to_json_safe, to_json_safe_dict
from app.services.market_data import (
    EmptyDataError,
    InvalidTickerError,
    MarketDataError,
    get_price_history,
)
from app.services.indicators import IndicatorInputError, add_technical_indicators
from app.services.live_market_data_service import get_live_market_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(tags=["market-data"])


class PriceHistorySummary(BaseModel):
    """Metadata summary for a ticker request."""

    ticker: str
    period: str
    rows: int
    start_date: str
    end_date: str
    latest_close: float


class PriceHistoryRow(BaseModel):
    """Typed daily OHLCV row."""

    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    adj_close: float | None
    volume: float | None


class IndicatorRow(BaseModel):
    """Typed row for indicator endpoint outputs."""

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
    ema_12: float | None
    ema_26: float | None
    rsi_14: float | None
    macd_line: float | None
    macd_signal: float | None
    macd_histogram: float | None
    avg_volume_20: float | None
    distance_from_52w_high_pct: float | None
    rolling_volatility_20_pct: float | None
    drawdown_from_peak_pct: float | None


class PriceHistoryResponse(BaseModel):
    """Response payload for /price-history."""

    summary: PriceHistorySummary
    latest_10_rows: list[PriceHistoryRow]


class IndicatorsResponse(BaseModel):
    """Response payload for /indicators."""

    ticker: str
    period: str
    latest_close: float
    latest_snapshot: IndicatorRow
    latest_30_rows: list[IndicatorRow]


class LiveMarketSnapshotResponse(BaseModel):
    """Typed response for near-live market snapshot endpoint."""

    ticker: str
    fetched_at_utc: str
    price_timestamp: str
    close: float
    open: float
    high: float
    low: float
    volume: float
    daily_change_pct: float
    pe_ratio: float | None = None
    market_cap: float | None = None
    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    business_summary: str | None = None
    data_freshness_note: str


@router.get("/price-history", response_model=PriceHistoryResponse)
def price_history(
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
) -> PriceHistoryResponse:
    """Get cleaned OHLCV history and return a summary with latest 10 rows."""
    logger.info("Request /price-history ticker=%s period=%s", ticker, period)
    try:
        df = get_price_history(ticker=ticker, period=period)
    except InvalidTickerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmptyDataError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MarketDataError as exc:
        logger.warning("Market data error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected error in /price-history")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc

    latest_df = df.tail(10).copy()
    latest_df["date"] = latest_df["date"].dt.strftime("%Y-%m-%d")

    summary = PriceHistorySummary(
        ticker=ticker.strip().upper(),
        period=period,
        rows=int(len(df)),
        start_date=df.iloc[0]["date"].strftime("%Y-%m-%d"),
        end_date=df.iloc[-1]["date"].strftime("%Y-%m-%d"),
        latest_close=float(df.iloc[-1]["close"]),
    )

    return PriceHistoryResponse(
        summary=summary,
        latest_10_rows=[
            PriceHistoryRow(**to_json_safe_dict(row))
            for row in latest_df.to_dict(orient="records")
        ],
    )


@router.get("/indicators", response_model=IndicatorsResponse)
def indicators(
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
) -> IndicatorsResponse:
    """Get latest indicator snapshot plus recent indicator rows."""
    logger.info("Request /indicators ticker=%s period=%s", ticker, period)
    try:
        price_df = get_price_history(ticker=ticker, period=period)
        indicators_df = add_technical_indicators(price_df)
    except InvalidTickerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmptyDataError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IndicatorInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MarketDataError as exc:
        logger.warning("Market data error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected error in /indicators")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc

    latest_row = indicators_df.iloc[-1].copy()
    latest_30_df = indicators_df.tail(30).copy()

    latest_row["date"] = latest_row["date"].strftime("%Y-%m-%d")
    latest_30_df["date"] = latest_30_df["date"].dt.strftime("%Y-%m-%d")

    return IndicatorsResponse(
        ticker=ticker.strip().upper(),
        period=period,
        latest_close=float(latest_row["close"]),
        latest_snapshot=IndicatorRow(**to_json_safe_dict(latest_row.to_dict())),
        latest_30_rows=[
            IndicatorRow(**to_json_safe_dict(row))
            for row in latest_30_df.to_dict(orient="records")
        ],
    )


@router.get("/market-data/live-snapshot", response_model=LiveMarketSnapshotResponse)
def market_data_live_snapshot(
    ticker: str = Query(
        ...,
        min_length=1,
        max_length=15,
        pattern=TICKER_PATTERN,
        description="Ticker symbol, e.g. VOO",
    ),
    period: str = Query(
        "3mo",
        pattern=PERIOD_PATTERN,
        description="History period for fallback context, e.g. 3mo",
    ),
) -> LiveMarketSnapshotResponse:
    """Return latest near-live market snapshot (provider-delayed if applicable)."""
    try:
        snapshot = get_live_market_snapshot(ticker=ticker, period=period)
        return LiveMarketSnapshotResponse(**to_json_safe_dict(snapshot))
    except (InvalidTickerError, EmptyDataError, MarketDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected error in /market-data/live-snapshot")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc
