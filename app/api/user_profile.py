"""Unified profile, settings, watchlist, and alert preference endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from typing import Literal

from app.models.user_profile import (
    AlertEnabledUsersResponse,
    UserAlertScanResponse,
    UserAlertSettingsResponse,
    UserAlertSettingsUpdateRequest,
    UserProfileResponse,
    UserProfileResetRequest,
    UserProfileSettingsUpdateRequest,
    UserWatchlistAddRequest,
    UserWatchlistRemoveRequest,
    UserWatchlistResponse,
)
from app.services.alert_settings_service import (
    get_user_alert_settings,
    scan_user_alerts,
    update_user_alert_settings,
)
from app.services.user_profile_service import (
    UserProfileError,
    UserProfileValidationError,
    get_user_profile_store,
)
from app.services.watchlist_service import (
    add_user_watchlist_ticker,
    get_user_watchlist,
    remove_user_watchlist_ticker,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["user-profile"])


@router.get("/user-profile", response_model=UserProfileResponse)
def get_user_profile(
    user_id: str = Query(..., min_length=1, max_length=120),
    display_name: str | None = Query(default=None, max_length=120),
    source: str | None = Query(default=None),
) -> UserProfileResponse:
    """Return one shared user profile, creating it on first access if needed."""
    try:
        return get_user_profile_store().get_or_create_profile(
            user_id=user_id,
            display_name=display_name,
            last_active_source=source,
        )
    except UserProfileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UserProfileError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/user-profile/settings", response_model=UserProfileResponse)
def update_user_profile_settings(
    request: UserProfileSettingsUpdateRequest,
) -> UserProfileResponse:
    """Update shared profile settings used by the dashboard and Discord bot."""
    try:
        return get_user_profile_store().update_profile_settings(request)
    except UserProfileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UserProfileError as exc:
        logger.exception("Unexpected profile settings update error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/user-profile/reset", response_model=UserProfileResponse)
def reset_user_profile(
    request: UserProfileResetRequest,
) -> UserProfileResponse:
    """Reset a shared user profile back to default settings."""
    try:
        return get_user_profile_store().reset_profile(request)
    except UserProfileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UserProfileError as exc:
        logger.exception("Unexpected profile reset error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/user-watchlist", response_model=UserWatchlistResponse)
def get_unified_user_watchlist(
    user_id: str = Query(..., min_length=1, max_length=120),
    market: Literal["US", "HK"] = "US",
) -> UserWatchlistResponse:
    """Return the resolved user watchlist or the system default fallback."""
    try:
        watchlist, using_system_default, profile = get_user_watchlist(
            user_id=user_id,
            market=market,
        )
        return UserWatchlistResponse(
            user_id=user_id,
            market=market,
            watchlist=watchlist,
            using_system_default=using_system_default,
            last_active_source=profile.last_active_source,
        )
    except UserProfileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/user-watchlist/add", response_model=UserWatchlistResponse)
def add_unified_user_watchlist_ticker(
    request: UserWatchlistAddRequest,
) -> UserWatchlistResponse:
    """Add one ticker to the shared user watchlist."""
    try:
        profile = add_user_watchlist_ticker(
            user_id=request.user_id,
            ticker=request.ticker,
            display_name=request.display_name,
            last_active_source=request.last_active_source,
            market=request.market,
        )
        watchlist, _, _ = get_user_watchlist(profile.user_id, market=request.market)
        return UserWatchlistResponse(
            user_id=profile.user_id,
            market=request.market,
            watchlist=watchlist,
            using_system_default=False,
            last_active_source=profile.last_active_source,
        )
    except UserProfileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/user-watchlist/remove", response_model=UserWatchlistResponse)
def remove_unified_user_watchlist_ticker(
    request: UserWatchlistRemoveRequest,
) -> UserWatchlistResponse:
    """Remove one ticker from the shared user watchlist."""
    try:
        profile = remove_user_watchlist_ticker(
            user_id=request.user_id,
            ticker=request.ticker,
            display_name=request.display_name,
            last_active_source=request.last_active_source,
            market=request.market,
        )
        watchlist, _, _ = get_user_watchlist(profile.user_id, market=request.market)
        return UserWatchlistResponse(
            user_id=profile.user_id,
            market=request.market,
            watchlist=watchlist,
            using_system_default=False,
            last_active_source=profile.last_active_source,
        )
    except UserProfileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/user-alert-settings", response_model=UserAlertSettingsResponse)
def get_unified_user_alert_settings(
    user_id: str = Query(..., min_length=1, max_length=120),
) -> UserAlertSettingsResponse:
    """Return shared alert preferences for one user."""
    try:
        settings_payload, using_fallback = get_user_alert_settings(user_id=user_id)
        return UserAlertSettingsResponse(
            **settings_payload,
            using_watchlist_fallback=using_fallback,
        )
    except UserProfileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/user-alert-settings/update", response_model=UserAlertSettingsResponse)
def update_unified_user_alert_settings(
    request: UserAlertSettingsUpdateRequest,
) -> UserAlertSettingsResponse:
    """Update shared alert preferences for one user."""
    try:
        profile = update_user_alert_settings(request)
        settings_payload, using_fallback = get_user_alert_settings(profile.user_id)
        return UserAlertSettingsResponse(
            **settings_payload,
            using_watchlist_fallback=using_fallback,
        )
    except UserProfileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UserProfileError as exc:
        logger.exception("Unexpected alert settings update error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/user-alerts/scan", response_model=UserAlertScanResponse)
def scan_unified_user_alerts(
    user_id: str = Query(..., min_length=1, max_length=120),
) -> UserAlertScanResponse:
    """Generate deduplicated alert events for one user profile."""
    try:
        return scan_user_alerts(user_id=user_id)
    except UserProfileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected user alert scan error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/user-alerts/enabled-users", response_model=AlertEnabledUsersResponse)
def list_alert_enabled_users() -> AlertEnabledUsersResponse:
    """Return alert-enabled users for scheduler-style integrations."""
    try:
        return AlertEnabledUsersResponse(
            users=get_user_profile_store().list_alert_enabled_user_summaries()
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected enabled-users listing error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
