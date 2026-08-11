"""Market-hours helpers for scheduler cadence decisions.

This module keeps the logic explicit and deterministic:
- market open window: 9:30 AM to 4:00 PM America/New_York
- weekends are always treated as market closed
- no exchange holiday calendar is applied yet (beginner-friendly baseline)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from app.services.market_config import MARKET_CONFIGS, normalize_market

MARKET_OPEN_INTERVAL_SECONDS = 5 * 60
MARKET_CLOSED_INTERVAL_SECONDS = 60 * 60

_EASTERN_TZ = ZoneInfo("America/New_York")
_MARKET_OPEN_TIME = time(9, 30)
_MARKET_CLOSE_TIME = time(16, 0)
_HK_TZ = ZoneInfo("Asia/Hong_Kong")
_HK_MORNING_OPEN = time(9, 30)
_HK_MORNING_CLOSE = time(12, 0)
_HK_AFTERNOON_OPEN = time(13, 0)
_HK_CLOSE = time(16, 0)


@dataclass(frozen=True)
class MarketHoursState:
    """Computed market-hours state used by the trader scheduler."""

    now_utc: datetime
    now_et: datetime
    is_weekend: bool
    is_market_open: bool
    mode: str
    interval_seconds: int
    market: str = "US"
    timezone: str = "America/New_York"


def _to_utc(value: datetime | None) -> datetime:
    """Normalize datetime input to timezone-aware UTC."""
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_market_open(now: datetime | None = None, market: str = "US") -> bool:
    """Return True during the configured market's continuous sessions."""
    clean_market = normalize_market(market)
    now_utc = _to_utc(now)
    local_now = now_utc.astimezone(_HK_TZ if clean_market == "HK" else _EASTERN_TZ)

    if local_now.weekday() >= 5:
        return False
    current_time = local_now.time()
    if clean_market == "HK":
        return (
            _HK_MORNING_OPEN <= current_time < _HK_MORNING_CLOSE
            or _HK_AFTERNOON_OPEN <= current_time < _HK_CLOSE
        )
    return _MARKET_OPEN_TIME <= current_time < _MARKET_CLOSE_TIME


def get_scheduler_interval_seconds(now: datetime | None = None, market: str = "US") -> int:
    """Return cadence based on current market-hours mode."""
    if is_market_open(now, market):
        return MARKET_OPEN_INTERVAL_SECONDS
    return MARKET_CLOSED_INTERVAL_SECONDS


def get_market_hours_state(now: datetime | None = None, market: str = "US") -> MarketHoursState:
    """Return detailed market-hours state for status displays/logging."""
    clean_market = normalize_market(market)
    now_utc = _to_utc(now)
    local_now = now_utc.astimezone(_HK_TZ if clean_market == "HK" else _EASTERN_TZ)
    open_now = is_market_open(now_utc, clean_market)
    return MarketHoursState(
        now_utc=now_utc,
        now_et=local_now,
        is_weekend=local_now.weekday() >= 5,
        is_market_open=open_now,
        mode="market_open" if open_now else "market_closed",
        interval_seconds=(
            MARKET_OPEN_INTERVAL_SECONDS if open_now else MARKET_CLOSED_INTERVAL_SECONDS
        ),
        market=clean_market,
        timezone=MARKET_CONFIGS[clean_market].timezone,
    )
