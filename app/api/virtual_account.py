"""Immutable virtual account endpoints (cash ledger + derived account view)."""

from __future__ import annotations

import logging
from time import perf_counter

from fastapi import APIRouter, HTTPException, Query

from app.models.account_ledger import (
    AccountLedgerListResponse,
    VirtualAccountEquityCurveResponse,
    VirtualAccountDepositRequest,
    VirtualAccountDiagnosticsResponse,
    VirtualAccountHistoryResponse,
    VirtualAccountHoldingsResponse,
    VirtualAccountRecentTradesResponse,
    VirtualAccountResetRequest,
    VirtualAccountResetResponse,
    VirtualAccountSummaryResponse,
    VirtualAccountWithdrawalRequest,
    VirtualTradingActivityResetRequest,
    VirtualTradingActivityResetResponse,
)
from app.models.monthly_contribution import (
    MonthlyContributionInputResponse,
    MonthlyContributionInputUpdateRequest,
)
from app.services.account_ledger_service import (
    AccountLedgerError,
    get_account_ledger_service,
)
from app.services.equity_curve_service import build_live_equity_curve
from app.services.monthly_contribution_service import get_monthly_contribution_store
from app.services.virtual_account_cache import (
    clear_user_virtual_account_cache,
    get_cached_equity_curve,
    get_cached_holdings,
    get_cached_summary,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["virtual-account"])


@router.get("/virtual-account/summary", response_model=VirtualAccountSummaryResponse)
def virtual_account_summary(
    user_id: str = Query(..., min_length=1, max_length=120),
) -> VirtualAccountSummaryResponse:
    """Return current account state rebuilt from immutable ledger events."""
    started = perf_counter()
    try:
        payload = get_cached_summary(
            user_id=user_id,
            loader=lambda: get_account_ledger_service().build_account_summary(user_id=user_id),
        )
        elapsed_ms = (perf_counter() - started) * 1000.0
        logger.info(
            "virtual-account summary user_id=%s holdings=%d elapsed_ms=%.1f",
            user_id,
            len(payload.get("holdings", [])),
            elapsed_ms,
        )
        return VirtualAccountSummaryResponse(**payload)
    except AccountLedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected virtual-account summary error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.get("/virtual-account/equity-curve", response_model=VirtualAccountEquityCurveResponse)
def virtual_account_equity_curve(
    user_id: str = Query(..., min_length=1, max_length=120),
    limit: int = Query(160, ge=1, le=1000),
) -> VirtualAccountEquityCurveResponse:
    """Return the profile-level live equity curve from the immutable ledger."""
    started = perf_counter()
    try:
        payload = get_cached_equity_curve(
            user_id=user_id,
            limit=limit,
            loader=lambda: build_live_equity_curve(user_id=user_id, limit=limit),
        )
        elapsed_ms = (perf_counter() - started) * 1000.0
        logger.info(
            "virtual-account equity-curve user_id=%s points=%d elapsed_ms=%.1f",
            user_id,
            len(payload.get("points", [])),
            elapsed_ms,
        )
        return VirtualAccountEquityCurveResponse(**payload)
    except AccountLedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected virtual-account equity curve error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.get(
    "/virtual-account/monthly-contribution-input",
    response_model=MonthlyContributionInputResponse,
)
def virtual_account_monthly_contribution_input(
    user_id: str = Query(..., min_length=1, max_length=120),
) -> MonthlyContributionInputResponse:
    """Return the active recurring monthly contribution input for one profile."""
    try:
        return get_monthly_contribution_store().get_active_input(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected virtual-account monthly contribution read error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.post(
    "/virtual-account/monthly-contribution-input",
    response_model=MonthlyContributionInputResponse,
)
def virtual_account_monthly_contribution_input_update(
    request: MonthlyContributionInputUpdateRequest,
) -> MonthlyContributionInputResponse:
    """Save recurring monthly contribution amount used for first-day auto-cash."""
    try:
        payload = get_monthly_contribution_store().set_active_input(
            user_id=request.user_id,
            amount=request.amount,
        )
        clear_user_virtual_account_cache(request.user_id)
        return payload
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected virtual-account monthly contribution update error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.get("/virtual-account/ledger", response_model=AccountLedgerListResponse)
def virtual_account_ledger(
    user_id: str = Query(..., min_length=1, max_length=120),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=100000),
) -> AccountLedgerListResponse:
    """List immutable ledger events for one user."""
    started = perf_counter()
    try:
        safe_limit = max(1, int(limit))
        safe_offset = max(0, int(offset))
        rows = get_account_ledger_service().list_events(
            user_id=user_id,
            limit=safe_limit + 1,
            offset=safe_offset,
        )
        has_more = len(rows) > safe_limit
        events = rows[:safe_limit]
        elapsed_ms = (perf_counter() - started) * 1000.0
        logger.info(
            "virtual-account ledger user_id=%s offset=%d limit=%d rows=%d has_more=%s elapsed_ms=%.1f",
            user_id,
            safe_offset,
            safe_limit,
            len(events),
            has_more,
            elapsed_ms,
        )
        return AccountLedgerListResponse(
            user_id=user_id,
            count=len(events),
            limit=safe_limit,
            offset=safe_offset,
            has_more=has_more,
            events=events,
        )
    except AccountLedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected virtual-account ledger error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.get("/virtual-account/history", response_model=VirtualAccountHistoryResponse)
def virtual_account_history(
    user_id: str = Query(..., min_length=1, max_length=120),
    limit: int = Query(120, ge=1, le=500),
    offset: int = Query(0, ge=0, le=100000),
) -> VirtualAccountHistoryResponse:
    """Return immutable account history with running balance for one profile."""
    # Pagination is important on small instances; full history rebuild can be expensive
    # for long-lived profiles, so we default to lighter pages.
    started = perf_counter()
    try:
        safe_limit = max(1, int(limit))
        safe_offset = max(0, int(offset))
        rows = get_account_ledger_service().list_account_history(
            user_id=user_id,
            limit=safe_limit + 1,
            offset=safe_offset,
        )
        has_more = len(rows) > safe_limit
        events = rows[:safe_limit]
        elapsed_ms = (perf_counter() - started) * 1000.0
        logger.info(
            "virtual-account history user_id=%s offset=%d limit=%d rows=%d has_more=%s elapsed_ms=%.1f",
            user_id,
            safe_offset,
            safe_limit,
            len(events),
            has_more,
            elapsed_ms,
        )
        return VirtualAccountHistoryResponse(
            user_id=user_id,
            count=len(events),
            limit=safe_limit,
            offset=safe_offset,
            has_more=has_more,
            events=events,
        )
    except AccountLedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected virtual-account history error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.get("/virtual-account/holdings", response_model=VirtualAccountHoldingsResponse)
def virtual_account_holdings(
    user_id: str = Query(..., min_length=1, max_length=120),
) -> VirtualAccountHoldingsResponse:
    """Return current open positions derived from immutable trade history."""
    started = perf_counter()
    try:
        holdings = get_cached_holdings(
            user_id=user_id,
            loader=lambda: get_cached_summary(
                user_id=user_id,
                loader=lambda: get_account_ledger_service().build_account_summary(user_id=user_id),
            ).get("holdings", []),
        )
        elapsed_ms = (perf_counter() - started) * 1000.0
        logger.info(
            "virtual-account holdings user_id=%s rows=%d elapsed_ms=%.1f",
            user_id,
            len(holdings),
            elapsed_ms,
        )
        return VirtualAccountHoldingsResponse(user_id=user_id, count=len(holdings), holdings=holdings)
    except AccountLedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected virtual-account holdings error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.get("/virtual-account/recent-trades", response_model=VirtualAccountRecentTradesResponse)
def virtual_account_recent_trades(
    user_id: str = Query(..., min_length=1, max_length=120),
    limit: int = Query(20, ge=1, le=200),
) -> VirtualAccountRecentTradesResponse:
    """Return recent executed buy/sell trades for one profile."""
    started = perf_counter()
    try:
        trades = get_account_ledger_service().list_recent_trade_events(user_id=user_id, limit=limit)
        elapsed_ms = (perf_counter() - started) * 1000.0
        logger.info(
            "virtual-account recent-trades user_id=%s limit=%d rows=%d elapsed_ms=%.1f",
            user_id,
            int(limit),
            len(trades),
            elapsed_ms,
        )
        return VirtualAccountRecentTradesResponse(user_id=user_id, count=len(trades), trades=trades)
    except AccountLedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected virtual-account recent trades error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.post("/virtual-account/deposit", response_model=VirtualAccountSummaryResponse)
def virtual_account_deposit(request: VirtualAccountDepositRequest) -> VirtualAccountSummaryResponse:
    """Create an immutable manual deposit event and return updated account summary."""
    try:
        ledger = get_account_ledger_service()
        ledger.create_manual_deposit(
            user_id=request.user_id,
            amount=request.amount,
            source=request.source,
            reason=request.reason,
        )
        clear_user_virtual_account_cache(request.user_id)
        return VirtualAccountSummaryResponse(**ledger.build_account_summary(request.user_id))
    except AccountLedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected virtual-account deposit error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.post("/virtual-account/withdraw", response_model=VirtualAccountSummaryResponse)
def virtual_account_withdraw(request: VirtualAccountWithdrawalRequest) -> VirtualAccountSummaryResponse:
    """Create an immutable withdrawal event and return updated account summary."""
    try:
        ledger = get_account_ledger_service()
        ledger.create_withdrawal(
            user_id=request.user_id,
            amount=request.amount,
            source=request.source,
            reason=request.reason,
        )
        clear_user_virtual_account_cache(request.user_id)
        return VirtualAccountSummaryResponse(**ledger.build_account_summary(request.user_id))
    except AccountLedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected virtual-account withdrawal error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.get("/virtual-account/diagnostics", response_model=VirtualAccountDiagnosticsResponse)
def virtual_account_diagnostics(
    user_id: str = Query(..., min_length=1, max_length=120),
) -> VirtualAccountDiagnosticsResponse:
    """Return profile-scoped persistence diagnostics."""
    try:
        payload = get_account_ledger_service().get_profile_diagnostics(user_id=user_id)
        return VirtualAccountDiagnosticsResponse(**payload)
    except AccountLedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected virtual-account diagnostics error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.post("/virtual-account/reset", response_model=VirtualAccountResetResponse)
def virtual_account_reset(request: VirtualAccountResetRequest) -> VirtualAccountResetResponse:
    """Reset one profile's simulated trading account data only."""
    if not bool(request.confirm_reset):
        raise HTTPException(
            status_code=400,
            detail="confirm_reset must be true to run a destructive account reset.",
        )
    try:
        payload = get_account_ledger_service().reset_profile_account_data(
            user_id=request.user_id,
            reset_monthly_contributions=bool(request.reset_monthly_contributions),
        )
        clear_user_virtual_account_cache(request.user_id)
        return VirtualAccountResetResponse(**payload)
    except AccountLedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected virtual-account reset error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.post(
    "/virtual-account/reset-trading-activity",
    response_model=VirtualTradingActivityResetResponse,
)
def virtual_trading_activity_reset(
    request: VirtualTradingActivityResetRequest,
) -> VirtualTradingActivityResetResponse:
    """Clear simulated trades and holdings without deleting profile funding."""
    if not bool(request.confirm_reset):
        raise HTTPException(
            status_code=400,
            detail="confirm_reset must be true to clear trading activity.",
        )
    try:
        payload = get_account_ledger_service().reset_profile_trading_activity(
            user_id=request.user_id,
        )
        clear_user_virtual_account_cache(request.user_id)
        return VirtualTradingActivityResetResponse(**payload)
    except AccountLedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected virtual trading activity reset error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc
