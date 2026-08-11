"""Typed user profile models shared by dashboard, Discord, and alerts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


LanguageMode = Literal["en", "zh", "bilingual"]
ActivitySource = Literal["discord", "dashboard"]
DeliverySource = Literal["discord", "dashboard"]
MarketCode = Literal["US", "HK"]


def _normalize_ticker_values(values: list[str]) -> list[str]:
    """Normalize watchlist-style ticker arrays in request and response models."""
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        clean = str(value).strip().upper()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)
    return normalized


class AlertSettingsResponse(BaseModel):
    """Serializable alert settings for one user."""

    alert_enabled: bool = True
    alert_threshold_high: int = Field(default=80, ge=0, le=100)
    alert_threshold_low: int = Field(default=45, ge=0, le=100)
    alert_watchlist: list[str] = Field(default_factory=list)
    preferred_delivery_source: DeliverySource = "discord"

    @field_validator("alert_watchlist", mode="before")
    @classmethod
    def validate_alert_watchlist(cls, value: list[str] | None) -> list[str]:
        return _normalize_ticker_values(value or [])


class UserProfileResponse(BaseModel):
    """Unified user profile returned by the backend."""

    user_id: str
    display_name: str | None = None
    preferred_language: LanguageMode = "bilingual"
    compact_mode: bool = False
    selected_evaluation_model: str = "logistic_regression"
    default_watchlist: list[str] = Field(default_factory=list)
    hk_watchlist: list[str] = Field(default_factory=list)
    alert_enabled: bool = True
    alert_threshold_high: int = Field(default=80, ge=0, le=100)
    alert_threshold_low: int = Field(default=45, ge=0, le=100)
    alert_watchlist: list[str] = Field(default_factory=list)
    preferred_delivery_source: DeliverySource = "discord"
    last_active_source: ActivitySource | None = None
    created_at: str
    updated_at: str

    @field_validator("default_watchlist", "hk_watchlist", "alert_watchlist", mode="before")
    @classmethod
    def validate_watchlists(cls, value: list[str] | None) -> list[str]:
        return _normalize_ticker_values(value or [])


class UserProfileSettingsUpdateRequest(BaseModel):
    """Request body for shared profile/settings updates."""

    user_id: str = Field(min_length=1, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    preferred_language: LanguageMode | None = None
    compact_mode: bool | None = None
    selected_evaluation_model: str | None = Field(default=None, min_length=1, max_length=80)
    default_watchlist: list[str] | None = None
    hk_watchlist: list[str] | None = None
    last_active_source: ActivitySource | None = None

    @field_validator("default_watchlist", "hk_watchlist", mode="before")
    @classmethod
    def validate_default_watchlist(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalize_ticker_values(value)


class UserProfileResetRequest(BaseModel):
    """Request body for resetting a shared user profile back to defaults."""

    user_id: str = Field(min_length=1, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    last_active_source: ActivitySource | None = None


class UserWatchlistResponse(BaseModel):
    """Response model for resolved user watchlists."""

    user_id: str
    market: MarketCode = "US"
    watchlist: list[str]
    using_system_default: bool
    last_active_source: ActivitySource | None = None


class UserWatchlistAddRequest(BaseModel):
    """Request body for adding one ticker to a user watchlist."""

    user_id: str = Field(min_length=1, max_length=120)
    market: MarketCode = "US"
    ticker: str = Field(min_length=1, max_length=20)
    display_name: str | None = Field(default=None, max_length=120)
    last_active_source: ActivitySource | None = None


class UserWatchlistRemoveRequest(BaseModel):
    """Request body for removing one ticker from a user watchlist."""

    user_id: str = Field(min_length=1, max_length=120)
    market: MarketCode = "US"
    ticker: str = Field(min_length=1, max_length=20)
    display_name: str | None = Field(default=None, max_length=120)
    last_active_source: ActivitySource | None = None


class UserAlertSettingsUpdateRequest(BaseModel):
    """Request body for updating unified alert preferences."""

    user_id: str = Field(min_length=1, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    alert_enabled: bool | None = None
    alert_threshold_high: int | None = Field(default=None, ge=0, le=100)
    alert_threshold_low: int | None = Field(default=None, ge=0, le=100)
    alert_watchlist: list[str] | None = None
    preferred_delivery_source: DeliverySource | None = None
    last_active_source: ActivitySource | None = None

    @field_validator("alert_watchlist", mode="before")
    @classmethod
    def validate_alert_watchlist(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalize_ticker_values(value)


class UserAlertSettingsResponse(BaseModel):
    """Response model for user alert preferences."""

    user_id: str
    alert_enabled: bool
    alert_threshold_high: int
    alert_threshold_low: int
    alert_watchlist: list[str]
    preferred_delivery_source: DeliverySource
    using_watchlist_fallback: bool


class UserAlertEventResponse(BaseModel):
    """One user-specific alert event ready for delivery."""

    ticker: str
    rule: str
    severity: str
    message_en: str
    message_zh: str
    suppressed_duplicate: bool = False


class UserAlertScanResponse(BaseModel):
    """Response model for scanning alerts for one user."""

    user_id: str
    alert_enabled: bool
    watchlist: list[str]
    alerts: list[UserAlertEventResponse]
    suppressed_count: int = 0


class AlertEnabledUserResponse(BaseModel):
    """Scheduler-friendly summary of one user with alerts enabled."""

    user_id: str
    display_name: str | None = None
    preferred_language: LanguageMode
    alert_enabled: bool
    alert_threshold_high: int
    alert_threshold_low: int
    alert_watchlist: list[str]
    preferred_delivery_source: DeliverySource
    last_active_source: ActivitySource | None = None


class AlertEnabledUsersResponse(BaseModel):
    """List response for users with alerts enabled."""

    users: list[AlertEnabledUserResponse]
