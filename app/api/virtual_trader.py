"""Live virtual trader endpoints (current simulation mode)."""

from __future__ import annotations

import logging
from time import perf_counter

from fastapi import APIRouter, HTTPException, Query

from app.models.live_virtual_trader import (
    LiveTraderRunRequest,
    LiveTraderStatusResponse,
    LiveTraderTradesResponse,
)
from app.services.live_virtual_trader import (
    AUTO_TRADING_MODEL_NAME,
    LiveVirtualTraderError,
    get_live_virtual_trader_status,
    list_live_virtual_trader_trades,
)
from app.services.trader_scheduler import (
    TraderSchedulerBusyError,
    get_trader_scheduler_service,
)
from app.services.virtual_account_cache import clear_user_virtual_account_cache

logger = logging.getLogger(__name__)

router = APIRouter(tags=["virtual-trader-live"])


@router.get("/virtual-trader/live-status", response_model=LiveTraderStatusResponse)
def get_virtual_trader_live_status(
    user_id: str = Query(..., min_length=1, max_length=120),
    ticker: str | None = Query(default=None, min_length=1, max_length=15),
    model_name: str | None = Query(default=None, min_length=1, max_length=80),
    auto_run: bool = Query(False),
) -> LiveTraderStatusResponse:
    """Return current live virtual trader status, with optional immediate run."""
    started = perf_counter()
    try:
        tickers = [ticker.strip().upper()] if ticker else None
        if auto_run:
            status = get_trader_scheduler_service().run_user_now(
                user_id=user_id,
                tickers=tickers,
                model_name=AUTO_TRADING_MODEL_NAME,
            )
            clear_user_virtual_account_cache(user_id)
        else:
            status = get_live_virtual_trader_status(
                user_id=user_id,
                tickers=tickers,
                model_name=AUTO_TRADING_MODEL_NAME,
                auto_run=False,
            )
        elapsed_ms = (perf_counter() - started) * 1000.0
        logger.info(
            "virtual-trader live-status user_id=%s auto_run=%s tickers_evaluated=%d failed=%d elapsed_ms=%.1f",
            user_id,
            bool(auto_run),
            int(status.tickers_evaluated),
            int(status.tickers_failed),
            elapsed_ms,
        )
        return LiveTraderStatusResponse(**status.__dict__)
    except TraderSchedulerBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LiveVirtualTraderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected live status error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.get("/virtual-trader/status", response_model=LiveTraderStatusResponse)
def get_virtual_trader_status_alias(
    user_id: str = Query(..., min_length=1, max_length=120),
    ticker: str | None = Query(default=None, min_length=1, max_length=15),
    model_name: str | None = Query(default=None, min_length=1, max_length=80),
    auto_run: bool = Query(False),
) -> LiveTraderStatusResponse:
    """Alias endpoint for live status to keep API naming simple for clients."""
    return get_virtual_trader_live_status(
        user_id=user_id,
        ticker=ticker,
        model_name=model_name,
        auto_run=auto_run,
    )


@router.post("/virtual-trader/run-now", response_model=LiveTraderStatusResponse)
def run_virtual_trader_now(request: LiveTraderRunRequest) -> LiveTraderStatusResponse:
    """Run live virtual trader decisions using the best available trading model."""
    try:
        status = get_trader_scheduler_service().run_user_now(
            user_id=request.user_id,
            tickers=request.tickers,
            model_name=AUTO_TRADING_MODEL_NAME,
        )
        clear_user_virtual_account_cache(request.user_id)
        return LiveTraderStatusResponse(**status.__dict__)
    except TraderSchedulerBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LiveVirtualTraderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected run-now error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.get("/virtual-trader/live-trades", response_model=LiveTraderTradesResponse)
def get_virtual_trader_live_trades(
    user_id: str = Query(..., min_length=1, max_length=120),
    ticker: str | None = Query(default=None, min_length=1, max_length=15),
    limit: int = Query(50, ge=1, le=500),
) -> LiveTraderTradesResponse:
    """Return recent live simulated trade/decision records."""
    started = perf_counter()
    try:
        payload = list_live_virtual_trader_trades(
            user_id=user_id,
            limit=limit,
            ticker=ticker.strip().upper() if ticker else None,
        )
        payload["count"] = len(payload.get("trades", []))
        elapsed_ms = (perf_counter() - started) * 1000.0
        logger.info(
            "virtual-trader live-trades user_id=%s limit=%d count=%d elapsed_ms=%.1f",
            user_id,
            int(limit),
            int(payload["count"]),
            elapsed_ms,
        )
        return LiveTraderTradesResponse(**payload)
    except LiveVirtualTraderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected live-trades error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.get("/virtual-trader/trades", response_model=LiveTraderTradesResponse)
def get_virtual_trader_trades_alias(
    user_id: str = Query(..., min_length=1, max_length=120),
    ticker: str | None = Query(default=None, min_length=1, max_length=15),
    limit: int = Query(50, ge=1, le=500),
) -> LiveTraderTradesResponse:
    """Alias endpoint for live trades."""
    return get_virtual_trader_live_trades(user_id=user_id, ticker=ticker, limit=limit)


@router.get("/virtual-trader/decisions", response_model=LiveTraderTradesResponse)
def get_virtual_trader_decisions_alias(
    user_id: str = Query(..., min_length=1, max_length=120),
    ticker: str | None = Query(default=None, min_length=1, max_length=15),
    limit: int = Query(20, ge=1, le=500),
) -> LiveTraderTradesResponse:
    """Decisions view currently mapped to live trade/decision log stream."""
    return get_virtual_trader_live_trades(user_id=user_id, ticker=ticker, limit=limit)
