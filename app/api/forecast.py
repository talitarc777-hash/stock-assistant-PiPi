"""API routes for scenario-based forecast output."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.api_utils import PERIOD_PATTERN, TICKER_PATTERN, to_json_safe
from app.services.forecast import ForecastInputError, build_scenario_forecast
from app.services.forecast_store import (
    ForecastSnapshot,
    ForecastStoreError,
    get_forecast_history,
    save_forecast_snapshot,
)
from app.services.indicators import IndicatorInputError, add_technical_indicators
from app.services.market_data import (
    EmptyDataError,
    InvalidTickerError,
    MarketDataError,
    get_price_history,
)
from app.services.market_config import MarketValidationError, resolve_security


logger = logging.getLogger(__name__)

router = APIRouter(tags=["forecast"])


class ForecastRangeResponse(BaseModel):
    """Expected scenario range bounds."""

    upper: float
    lower: float


class ForecastLevelsResponse(BaseModel):
    """Support and resistance reference levels."""

    support_level: float
    resistance_level: float


class ForecastResponse(BaseModel):
    """Scenario-based forecast response.

    This is a scenario-based forecast, not a guaranteed prediction.
    It does not predict exact future prices.
    """

    ticker: str
    market: Literal["US", "HK"] = "US"
    provider_symbol: str | None = None
    currency: str = "USD"
    currency_symbol: str = "$"
    current_close: float
    trend_regime: str
    trend_regime_en: str
    trend_regime_zh: str
    forecast_summary_en: str
    forecast_summary_zh: str
    forecast_summary_bilingual: str
    outlook_5d: str = Field(
        description="Scenario-based 5-day outlook, not a guaranteed prediction."
    )
    outlook_20d: str = Field(
        description="Scenario-based 20-day outlook, not a guaranteed prediction."
    )
    expected_range: ForecastRangeResponse
    levels: ForecastLevelsResponse
    confidence_score: int
    explanation_bullets: list[str]


class ForecastHistoryItemResponse(BaseModel):
    """One persisted forecast snapshot item."""

    timestamp_utc: str
    ticker: str
    close: float
    trend_regime: str
    outlook_5d: str
    outlook_20d: str
    expected_range: ForecastRangeResponse
    confidence_score: int


class ForecastHistoryResponse(BaseModel):
    """Forecast snapshot history for one ticker."""

    ticker: str
    count: int
    snapshots: list[ForecastHistoryItemResponse]


@router.get("/forecast", response_model=ForecastResponse)
def forecast_ticker(
    ticker: str = Query(
        ...,
        min_length=1,
        max_length=15,
        pattern=TICKER_PATTERN,
        description="Ticker symbol, e.g. VOO",
    ),
    period: str = Query(
        "2y",
        pattern=PERIOD_PATTERN,
        description="History period, e.g. 1y, 2y, 5y, max",
    ),
    market: Literal["US", "HK"] = "US",
) -> ForecastResponse:
    """
    Return a scenario-based forecast, not a guaranteed prediction.

    This endpoint does not predict exact prices.
    """
    try:
        identity = resolve_security(ticker, market)
        logger.info(
            "Request /forecast ticker=%s period=%s market=%s",
            identity.ticker,
            period,
            identity.market,
        )
        price_df = get_price_history(
            ticker=identity.ticker,
            period=period,
            market=identity.market,
        )
        indicators_df = add_technical_indicators(price_df)
        forecast = build_scenario_forecast(indicators_df)
    except (InvalidTickerError, MarketValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmptyDataError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (IndicatorInputError, ForecastInputError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected error in /forecast")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc

    current_close = float(indicators_df.iloc[-1]["close"])
    try:
        save_forecast_snapshot(
            ticker=identity.provider_symbol,
            close=current_close,
            trend_regime=forecast.trend_regime,
            outlook_5d=forecast.forecast_horizon_5d,
            outlook_20d=forecast.forecast_horizon_20d,
            expected_range_lower=forecast.expected_range_lower,
            expected_range_upper=forecast.expected_range_upper,
            confidence_score=forecast.confidence_score,
        )
    except ForecastStoreError as exc:
        logger.exception("Failed to persist forecast snapshot")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ForecastResponse(
        ticker=identity.ticker,
        market=identity.market,
        provider_symbol=identity.provider_symbol,
        currency=identity.currency,
        currency_symbol=identity.currency_symbol,
        current_close=float(to_json_safe(current_close)),
        trend_regime=forecast.trend_regime,
        trend_regime_en=forecast.trend_regime,
        trend_regime_zh=forecast.trend_regime_zh,
        forecast_summary_en=forecast.forecast_summary_en,
        forecast_summary_zh=forecast.forecast_summary_zh,
        forecast_summary_bilingual=forecast.forecast_summary_bilingual,
        outlook_5d=forecast.forecast_horizon_5d,
        outlook_20d=forecast.forecast_horizon_20d,
        expected_range=ForecastRangeResponse(
            upper=float(to_json_safe(forecast.expected_range_upper)),
            lower=float(to_json_safe(forecast.expected_range_lower)),
        ),
        levels=ForecastLevelsResponse(
            support_level=float(to_json_safe(forecast.support_level)),
            resistance_level=float(to_json_safe(forecast.resistance_level)),
        ),
        confidence_score=forecast.confidence_score,
        explanation_bullets=forecast.explanation_bullets,
    )


@router.get("/forecast-history", response_model=ForecastHistoryResponse)
def forecast_history(
    ticker: str = Query(
        ...,
        min_length=1,
        max_length=15,
        pattern=TICKER_PATTERN,
        description="Ticker symbol, e.g. VOO",
    ),
    limit: int = Query(
        200,
        ge=1,
        le=1000,
        description="Maximum number of snapshots to return (1-1000).",
    ),
) -> ForecastHistoryResponse:
    """Return persisted forecast snapshots for future forecast-vs-actual evaluation."""
    logger.info("Request /forecast-history ticker=%s limit=%s", ticker, limit)
    try:
        snapshots: list[ForecastSnapshot] = get_forecast_history(ticker=ticker, limit=limit)
    except ForecastStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected error in /forecast-history")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc

    return ForecastHistoryResponse(
        ticker=ticker.strip().upper(),
        count=len(snapshots),
        snapshots=[
            ForecastHistoryItemResponse(
                timestamp_utc=item.timestamp_utc,
                ticker=item.ticker,
                close=float(to_json_safe(item.close)),
                trend_regime=item.trend_regime,
                outlook_5d=item.outlook_5d,
                outlook_20d=item.outlook_20d,
                expected_range=ForecastRangeResponse(
                    lower=float(to_json_safe(item.expected_range_lower)),
                    upper=float(to_json_safe(item.expected_range_upper)),
                ),
                confidence_score=item.confidence_score,
            )
            for item in snapshots
        ],
    )
