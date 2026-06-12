"""U.S. market-hours helpers for scheduler cadence decisions.

This module keeps the logic explicit and deterministic:
- market open window: 9:30 AM to 4:00 PM America/New_York
- weekends are always treated as market closed
- no exchange holiday calendar is applied yet (beginner-friendly baseline)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

MARKET_OPEN_INTERVAL_SECONDS = 5 * 60
MARKET_CLOSED_INTERVAL_SECONDS = 60 * 60

_EASTERN_TZ = ZoneInfo("America/New_York")
_MARKET_OPEN_TIME = time(9, 30)
_MARKET_CLOSE_TIME = time(16, 0)


@dataclass(frozen=True)
class MarketHoursState:
    """Computed market-hours state used by the trader scheduler."""

    now_utc: datetime
    now_et: datetime
    is_weekend: bool
    is_market_open: bool
    mode: str
    interval_seconds: int


def _to_utc(value: datetime | None) -> datetime:
    """Normalize datetime input to timezone-aware UTC."""
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_market_open(now: datetime | None = None) -> bool:
    """Return True when the U.S. market session is open."""
    now_utc = _to_utc(now)
    now_et = now_utc.astimezone(_EASTERN_TZ)

    if now_et.weekday() >= 5:
        return False
    current_time = now_et.time()
    return _MARKET_OPEN_TIME <= current_time < _MARKET_CLOSE_TIME


def get_scheduler_interval_seconds(now: datetime | None = None) -> int:
    """Return cadence based on current market-hours mode."""
    if is_market_open(now):
        return MARKET_OPEN_INTERVAL_SECONDS
    return MARKET_CLOSED_INTERVAL_SECONDS


def get_market_hours_state(now: datetime | None = None) -> MarketHoursState:
    """Return detailed market-hours state for status displays/logging."""
    now_utc = _to_utc(now)
    now_et = now_utc.astimezone(_EASTERN_TZ)
    open_now = is_market_open(now_utc)
    return MarketHoursState(
        now_utc=now_utc,
        now_et=now_et,
        is_weekend=now_et.weekday() >= 5,
        is_market_open=open_now,
        mode="market_open" if open_now else "market_closed",
        interval_seconds=(
            MARKET_OPEN_INTERVAL_SECONDS if open_now else MARKET_CLOSED_INTERVAL_SECONDS
        ),
    )
