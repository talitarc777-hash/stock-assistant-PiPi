"""Discord alerts for unusually large virtual-trader activity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.settings import get_settings
from app.services.user_profile_service import get_user_profile_store

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VirtualTradeActivityAlert:
    """A Discord-ready alert for a ticker/action trade burst."""

    user_id: str
    ticker: str
    action: str
    window_minutes: int
    trade_count: int
    total_value_hkd: float
    threshold_hkd: float
    latest_trade_value_hkd: float
    state_key: str
    message: str


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _trade_value_hkd(trade: dict[str, Any]) -> float:
    quantity = float(trade.get("quantity") or 0.0)
    price = float(trade.get("price") or 0.0)
    return abs(quantity * price)


def build_virtual_trade_activity_alert(
    *,
    current_trade: dict[str, Any],
    recent_trades: list[dict[str, Any]],
    window_minutes: int,
    large_value_threshold_hkd: float,
    min_trade_count: int,
) -> VirtualTradeActivityAlert | None:
    """Return an alert if the latest trade is a large single trade or burst."""
    action = str(current_trade.get("action") or "").lower()
    if action not in {"buy", "sell"}:
        return None

    ticker = str(current_trade.get("ticker") or "").upper()
    user_id = str(current_trade.get("user_id") or "").strip()
    latest_ts = _parse_iso_timestamp(current_trade.get("timestamp"))
    if not ticker or not user_id or latest_ts is None:
        return None

    window_start = latest_ts - timedelta(minutes=max(1, int(window_minutes)))
    same_window: list[dict[str, Any]] = []
    for trade in recent_trades:
        if str(trade.get("ticker") or "").upper() != ticker:
            continue
        if str(trade.get("action") or "").lower() != action:
            continue
        trade_ts = _parse_iso_timestamp(trade.get("timestamp"))
        if trade_ts is None or trade_ts < window_start or trade_ts > latest_ts + timedelta(seconds=5):
            continue
        value = _trade_value_hkd(trade)
        if value <= 0:
            continue
        same_window.append(trade)

    latest_value = _trade_value_hkd(current_trade)
    total_value = sum(_trade_value_hkd(trade) for trade in same_window)
    trade_count = len(same_window)
    is_large_single = latest_value >= large_value_threshold_hkd
    is_burst = trade_count >= min_trade_count and total_value >= large_value_threshold_hkd
    if not is_large_single and not is_burst:
        return None

    state_bucket = latest_ts.strftime("%Y%m%d%H%M")
    state_key = (
        f"{action}:{state_bucket}:"
        f"{int(total_value // max(1.0, large_value_threshold_hkd))}:"
        f"{trade_count}"
    )
    action_label = "buying" if action == "buy" else "selling"
    message = (
        f"Virtual Trader alert: {ticker} has large simulated {action_label} activity.\n"
        f"- Window: {window_minutes} minutes\n"
        f"- Trades: {trade_count}\n"
        f"- Total value: HKD {total_value:,.0f}\n"
        f"- Latest trade: HKD {latest_value:,.0f}\n"
        f"- Alert threshold: HKD {large_value_threshold_hkd:,.0f}\n"
        "This is a paper-trading alert for monitoring only, not financial advice."
    )
    return VirtualTradeActivityAlert(
        user_id=user_id,
        ticker=ticker,
        action=action,
        window_minutes=window_minutes,
        trade_count=trade_count,
        total_value_hkd=total_value,
        threshold_hkd=large_value_threshold_hkd,
        latest_trade_value_hkd=latest_value,
        state_key=state_key,
        message=message,
    )


def send_discord_webhook_message(webhook_url: str, message: str, timeout_seconds: float = 4.0) -> None:
    """Send a plain Discord webhook message."""
    payload = json.dumps({"content": message}, ensure_ascii=False).encode("utf-8")
    request = Request(
        webhook_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "StockAssistantPiPi/1.0",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        status = getattr(response, "status", 204)
        if status >= 400:
            raise RuntimeError(f"Discord webhook failed with HTTP {status}")


def maybe_send_virtual_trade_discord_alert(
    *,
    current_trade: dict[str, Any],
    recent_trades: list[dict[str, Any]],
) -> VirtualTradeActivityAlert | None:
    """Detect and send a Discord alert for large short-window trade activity."""
    settings = get_settings()
    if not settings.virtual_trade_discord_alert_enabled:
        return None

    alert = build_virtual_trade_activity_alert(
        current_trade=current_trade,
        recent_trades=recent_trades,
        window_minutes=settings.virtual_trade_alert_window_minutes,
        large_value_threshold_hkd=settings.virtual_trade_large_value_hkd_threshold,
        min_trade_count=settings.virtual_trade_alert_min_trade_count,
    )
    if alert is None:
        return None

    store = get_user_profile_store()
    profile = store.get_or_create_profile(alert.user_id)
    if not bool(profile.alert_enabled) or str(profile.preferred_delivery_source) != "discord":
        logger.info(
            "Virtual trade Discord alert skipped by shared preference user_id=%s enabled=%s delivery=%s",
            alert.user_id,
            profile.alert_enabled,
            profile.preferred_delivery_source,
        )
        return None
    rule = f"virtual_trade_large_{alert.action}"
    should_send = store.is_alert_state_new(
        alert.user_id,
        alert.ticker,
        rule,
        alert.state_key,
    )
    if not should_send:
        logger.info(
            "Virtual trade Discord alert suppressed user_id=%s ticker=%s action=%s state=%s",
            alert.user_id,
            alert.ticker,
            alert.action,
            alert.state_key,
        )
        return None

    if not settings.discord_webhook_url:
        logger.warning(
            "Virtual trade alert detected but DISCORD_WEBHOOK_URL is not configured: %s",
            alert.message,
        )
        return alert

    try:
        send_discord_webhook_message(settings.discord_webhook_url, alert.message)
        store.record_alert_dispatched(
            alert.user_id,
            alert.ticker,
            rule,
            alert.state_key,
        )
        logger.info(
            "Virtual trade Discord alert sent user_id=%s ticker=%s action=%s value=%.2f",
            alert.user_id,
            alert.ticker,
            alert.action,
            alert.total_value_hkd,
        )
    except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
        logger.warning(
            "Virtual trade Discord alert failed user_id=%s ticker=%s action=%s error=%s",
            alert.user_id,
            alert.ticker,
            alert.action,
            exc,
        )
    return alert
