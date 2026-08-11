"""Thin helpers around the shared user-profile watchlist storage."""

from __future__ import annotations

import logging

from app.models.user_profile import UserProfileResponse
from app.services.hkex_security_metadata import get_hk_security_metadata
from app.services.market_config import MarketValidationError, normalize_market, resolve_security
from app.services.user_profile_service import get_user_profile_store
from app.services.user_profile_service import UserProfileValidationError

logger = logging.getLogger(__name__)


def get_user_watchlist(
    user_id: str,
    market: str = "US",
) -> tuple[list[str], bool, UserProfileResponse]:
    """Return a user's watchlist and whether the system default is being used."""
    return get_user_profile_store().get_effective_watchlist(
        user_id=user_id,
        market=market,
    )


def add_user_watchlist_ticker(
    user_id: str,
    ticker: str,
    display_name: str | None = None,
    last_active_source: str | None = None,
    market: str = "US",
) -> UserProfileResponse:
    """Add one ticker to the shared user watchlist."""
    try:
        clean_market = normalize_market(market)
        identity = resolve_security(ticker, clean_market)
        if clean_market == "HK" and get_hk_security_metadata(identity.ticker) is None:
            raise UserProfileValidationError(
                "Ticker is not present in the cached official HKEX securities list."
            )
        profile = get_user_profile_store().add_watchlist_ticker(
            user_id=user_id,
            ticker=identity.ticker,
            display_name=display_name,
            last_active_source=last_active_source,
            market=clean_market,
        )
        if clean_market == "HK":
            # Lazy import avoids coupling profile-store initialization to the
            # heavier live-trader/model runtime.
            from app.services.live_virtual_trader import (
                ensure_active_ticker_model_training,
            )

            try:
                ensure_active_ticker_model_training(identity.ticker, market=clean_market)
            except Exception as exc:  # pragma: no cover - background readiness guard
                # Enrollment is already safely persisted. A later decision or
                # lifecycle cycle will retry model readiness.
                logger.warning(
                    "HK ticker activation persisted but model queue check failed ticker=%s error=%s",
                    identity.ticker,
                    exc,
                )
        return profile
    except MarketValidationError as exc:
        raise UserProfileValidationError(str(exc)) from exc


def remove_user_watchlist_ticker(
    user_id: str,
    ticker: str,
    display_name: str | None = None,
    last_active_source: str | None = None,
    market: str = "US",
) -> UserProfileResponse:
    """Remove one ticker from the shared user watchlist."""
    try:
        clean_market = normalize_market(market)
        identity = resolve_security(ticker, clean_market)
        return get_user_profile_store().remove_watchlist_ticker(
            user_id=user_id,
            ticker=identity.ticker,
            display_name=display_name,
            last_active_source=last_active_source,
            market=clean_market,
        )
    except MarketValidationError as exc:
        raise UserProfileValidationError(str(exc)) from exc
