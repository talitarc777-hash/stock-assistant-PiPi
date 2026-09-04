"""API endpoint for explainable stock analysis scoring."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.models.ticker_classification import ClassifiedTickerResponse

from app.core.api_utils import (
    PERIOD_PATTERN,
    TICKER_PATTERN,
    parse_ticker_csv,
    to_json_safe,
    to_json_safe_dict,
)
from app.services.benchmark import (
    BenchmarkAnalysisError,
    compare_to_benchmark,
)
from app.services.indicators import IndicatorInputError, add_technical_indicators
from app.services.market_data import (
    EmptyDataError,
    InvalidTickerError,
    MarketDataError,
    get_price_history,
)
from app.services.market_config import (
    MARKET_CONFIGS,
    MarketValidationError,
    normalize_market,
    resolve_security,
)
from app.services.live_market_data_service import get_security_profile
from app.services.scoring import ScoringInputError, score_from_indicators
from app.core.translation_terms import (
    translate_action_summary_to_zh,
    translate_explanation_bullets,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analysis"])


class ScoreBreakdownResponse(BaseModel):
    """Serializable score breakdown fields."""

    trend_score: int
    momentum_score: int
    confirmation_score: int
    risk_penalty: int
    total_score: int


class FailedTickerResponse(BaseModel):
    """Per-ticker failure details for batch endpoints."""

    ticker: str
    error: str


class BenchmarkRelativeResponse(BaseModel):
    """Typed benchmark-relative output."""

    benchmark: str
    returns_pct: dict[str, float | None]
    benchmark_returns_pct: dict[str, float | None]
    excess_returns_pct: dict[str, float | None]
    benchmark_strength_score: int


class AnalyzeResponse(ClassifiedTickerResponse):
    """Response model for the /analyze endpoint."""

    market: Literal["US", "HK"] = "US"
    provider_symbol: str | None = None
    currency: str = "USD"
    currency_symbol: str = "$"
    ticker_name: str | None = None
    ticker_name_en: str | None = None
    ticker_name_zh: str | None = None
    company_name: str | None = None
    company_name_zh: str | None = None
    security_name: str | None = None
    security_name_zh: str | None = None
    latest_close: float
    score_breakdown: ScoreBreakdownResponse
    label: str
    action_summary: str
    action_summary_en: str
    action_summary_zh: str
    action_summary_bilingual: str
    explanation_bullets: list[str]
    explanation_bullets_en: list[str]
    explanation_bullets_zh: list[str]
    explanation_bullets_bilingual: list[str]
    benchmark_relative: BenchmarkRelativeResponse


class BenchmarkCompareResponse(BaseModel):
    """Response model for /compare-to-benchmark."""

    ticker: str
    benchmark: str
    period: str
    performance: BenchmarkRelativeResponse


class WatchlistAnalyzeResponse(BaseModel):
    """Response model for /watchlist-analyze."""

    benchmark: str
    period: str
    ranked_results: list[AnalyzeResponse]
    failed_tickers: list[FailedTickerResponse]


def _build_ticker_analysis_response(
    ticker: str,
    period: str,
    benchmark: str,
    benchmark_df,
    market: str = "US",
) -> AnalyzeResponse:
    """Build a single-ticker analysis response for reuse across endpoints."""
    identity = resolve_security(ticker, market)
    price_df = get_price_history(ticker=identity.ticker, period=period, market=identity.market)
    indicators_df = add_technical_indicators(price_df)
    score = score_from_indicators(indicators_df)
    benchmark_cmp = compare_to_benchmark(
        ticker_df=price_df,
        benchmark_df=benchmark_df,
        benchmark_symbol=benchmark,
    )

    latest_close = to_json_safe(float(indicators_df.iloc[-1]["close"]))

    benchmark_relative = BenchmarkRelativeResponse(
        benchmark=benchmark_cmp.benchmark,
        returns_pct=to_json_safe_dict(benchmark_cmp.returns),
        benchmark_returns_pct=to_json_safe_dict(benchmark_cmp.benchmark_returns),
        excess_returns_pct=to_json_safe_dict(benchmark_cmp.excess_returns),
        benchmark_strength_score=benchmark_cmp.benchmark_strength_score,
    )

    explanation_bullets_zh, explanation_bullets_bilingual = translate_explanation_bullets(
        score.explanations
    )
    action_summary_zh = translate_action_summary_to_zh(score.action_summary)
    # A display-name lookup is helpful but must never make the existing
    # technical analysis endpoint unavailable when a metadata provider is
    # temporarily down.
    try:
        profile = get_security_profile(identity.ticker, identity.market)
    except Exception:
        profile = {}

    return AnalyzeResponse(
        ticker=identity.ticker,
        market=identity.market,
        provider_symbol=identity.provider_symbol,
        currency=identity.currency,
        currency_symbol=identity.currency_symbol,
        ticker_name=profile.get("ticker_name"),
        ticker_name_en=profile.get("ticker_name_en"),
        ticker_name_zh=profile.get("ticker_name_zh"),
        company_name=profile.get("company_name"),
        company_name_zh=profile.get("company_name_zh"),
        security_name=profile.get("security_name"),
        security_name_zh=profile.get("security_name_zh"),
        latest_close=float(latest_close),
        score_breakdown=ScoreBreakdownResponse(
            trend_score=score.trend_score,
            momentum_score=score.momentum_score,
            confirmation_score=score.confirmation_score,
            risk_penalty=score.risk_penalty,
            total_score=score.total_score,
        ),
        label=score.label,
        action_summary=score.action_summary,
        action_summary_en=score.action_summary,
        action_summary_zh=action_summary_zh,
        action_summary_bilingual=f"{score.action_summary} / {action_summary_zh}",
        explanation_bullets=score.explanations,
        explanation_bullets_en=score.explanations,
        explanation_bullets_zh=explanation_bullets_zh,
        explanation_bullets_bilingual=explanation_bullets_bilingual,
        benchmark_relative=benchmark_relative,
    )


@router.get("/analyze", response_model=AnalyzeResponse)
def analyze_ticker(
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
    benchmark: str | None = Query(
        None,
        min_length=1,
        max_length=15,
        pattern=TICKER_PATTERN,
        description="Benchmark symbol, default VOO",
    ),
    market: Literal["US", "HK"] = "US",
) -> AnalyzeResponse:
    """Run indicator + scoring analysis for one ticker with deterministic rules."""
    clean_market = normalize_market(market)
    benchmark_symbol = benchmark or MARKET_CONFIGS[clean_market].default_benchmark
    logger.info(
        "Request /analyze ticker=%s period=%s benchmark=%s market=%s",
        ticker,
        period,
        benchmark_symbol,
        clean_market,
    )
    try:
        benchmark_df = get_price_history(
            ticker=benchmark_symbol,
            period=period,
            market=clean_market,
        )
        return _build_ticker_analysis_response(
            ticker=ticker,
            period=period,
            benchmark=benchmark_symbol,
            benchmark_df=benchmark_df,
            market=clean_market,
        )
    except (InvalidTickerError, MarketValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmptyDataError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (IndicatorInputError, ScoringInputError, BenchmarkAnalysisError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MarketDataError as exc:
        logger.warning("Market data error during analysis: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected error in /analyze")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.get("/compare-to-benchmark", response_model=BenchmarkCompareResponse)
def compare_benchmark_endpoint(
    ticker: str = Query(
        ...,
        min_length=1,
        max_length=15,
        pattern=TICKER_PATTERN,
        description="Ticker symbol, e.g. QQQ",
    ),
    benchmark: str = Query(
        "VOO",
        min_length=1,
        max_length=15,
        pattern=TICKER_PATTERN,
        description="Benchmark symbol, default VOO",
    ),
    period: str = Query(
        "5y",
        pattern=PERIOD_PATTERN,
        description="History period, e.g. 1y, 5y, max",
    ),
) -> BenchmarkCompareResponse:
    """Compare ticker performance against a benchmark over 1m/3m/6m/12m periods."""
    logger.info(
        "Request /compare-to-benchmark ticker=%s benchmark=%s period=%s",
        ticker,
        benchmark,
        period,
    )
    try:
        ticker_df = get_price_history(ticker=ticker, period=period)
        benchmark_df = get_price_history(ticker=benchmark, period=period)
        cmp = compare_to_benchmark(
            ticker_df=ticker_df,
            benchmark_df=benchmark_df,
            benchmark_symbol=benchmark,
        )
    except InvalidTickerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmptyDataError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (MarketDataError, BenchmarkAnalysisError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected error in /compare-to-benchmark")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc

    performance = BenchmarkRelativeResponse(
        benchmark=cmp.benchmark,
        returns_pct=to_json_safe_dict(cmp.returns),
        benchmark_returns_pct=to_json_safe_dict(cmp.benchmark_returns),
        excess_returns_pct=to_json_safe_dict(cmp.excess_returns),
        benchmark_strength_score=cmp.benchmark_strength_score,
    )

    return BenchmarkCompareResponse(
        ticker=ticker.strip().upper(),
        benchmark=cmp.benchmark,
        period=period,
        performance=performance,
    )


@router.get("/watchlist-analyze", response_model=WatchlistAnalyzeResponse)
def watchlist_analyze(
    tickers: str = Query(..., description="Comma-separated tickers, e.g. VOO,SPY,QQQ"),
    period: str = Query(
        "5y",
        pattern=PERIOD_PATTERN,
        description="History period, e.g. 1y, 5y, max",
    ),
    benchmark: str = Query(
        "VOO",
        min_length=1,
        max_length=15,
        pattern=TICKER_PATTERN,
        description="Benchmark symbol, default VOO",
    ),
) -> WatchlistAnalyzeResponse:
    """Analyze a comma-separated ticker list and rank by score descending."""
    logger.info(
        "Request /watchlist-analyze tickers=%s period=%s benchmark=%s",
        tickers,
        period,
        benchmark,
    )
    try:
        ticker_list = parse_ticker_csv(tickers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        benchmark_df = get_price_history(ticker=benchmark, period=period)
    except (InvalidTickerError, EmptyDataError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid benchmark: {exc}") from exc
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected error in /watchlist-analyze benchmark setup")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc

    ranked_results: list[AnalyzeResponse] = []
    failed_tickers: list[FailedTickerResponse] = []

    for ticker in ticker_list:
        try:
            result = _build_ticker_analysis_response(
                ticker=ticker,
                period=period,
                benchmark=benchmark,
                benchmark_df=benchmark_df,
            )
            ranked_results.append(result)
        except (InvalidTickerError, EmptyDataError, IndicatorInputError, ScoringInputError, BenchmarkAnalysisError, MarketDataError) as exc:
            logger.warning("Watchlist analysis failed for ticker=%s: %s", ticker, exc)
            failed_tickers.append(FailedTickerResponse(ticker=ticker, error=str(exc)))
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("Unexpected watchlist analysis failure for ticker=%s", ticker)
            failed_tickers.append(
                FailedTickerResponse(ticker=ticker, error=f"{type(exc).__name__}: {exc}")
            )

    ranked_results.sort(
        key=lambda item: item.score_breakdown.total_score,
        reverse=True,
    )

    return WatchlistAnalyzeResponse(
        benchmark=benchmark.strip().upper(),
        period=period,
        ranked_results=ranked_results,
        failed_tickers=failed_tickers,
    )
