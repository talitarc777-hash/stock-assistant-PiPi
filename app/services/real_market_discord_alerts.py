"""Discord alerts for unusual real-market activity.

This uses delayed/free market candles, so it detects directional pressure:
- high traded value
- volume spike versus recent candles
- short-window price move

It does not claim to know exact buyer-initiated or seller-initiated order flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import math
from typing import Any, Callable

import pandas as pd
import yfinance as yf

from app.core.settings import get_settings
from app.services.user_profile_service import get_user_profile_store
from app.services.virtual_trade_discord_alerts import send_discord_webhook_message

logger = logging.getLogger(__name__)

IntradayDownloadFn = Callable[[str, str, str], pd.DataFrame]


@dataclass(frozen=True)
class RealMarketActivityAlert:
    """A Discord-ready alert for unusual real-market directional pressure."""

    user_id: str
    ticker: str
    alert_type: str
    pressure: str
    window_minutes: int
    price_change_pct: float
    latest_close: float
    window_volume: float
    average_window_volume: float
    volume_spike_ratio: float
    traded_value: float
    threshold_value: float
    price_threshold_pct: float
    state_key: str
    message: str


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _normalize_tickers(tickers: list[str]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for ticker in tickers:
        symbol = str(ticker or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        values.append(symbol)
    return values


def _default_download_intraday(ticker: str, period: str, interval: str) -> pd.DataFrame:
    return yf.download(
        tickers=ticker,
        period=period,
        interval=interval,
        auto_adjust=False,
        progress=False,
        threads=False,
    )


def _clean_intraday_frame(raw_df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw_df is None or raw_df.empty:
        return pd.DataFrame()

    frame = raw_df.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        level_values = [set(frame.columns.get_level_values(i)) for i in range(frame.columns.nlevels)]
        if ticker in level_values[-1]:
            frame = frame.xs(ticker, axis=1, level=frame.columns.nlevels - 1, drop_level=True)
        else:
            frame.columns = [column[0] for column in frame.columns]

    if "timestamp" not in frame.columns:
        frame = frame.reset_index()
        if "Datetime" in frame.columns:
            frame = frame.rename(columns={"Datetime": "timestamp"})
        elif "Date" in frame.columns:
            frame = frame.rename(columns={"Date": "timestamp"})
        elif "index" in frame.columns:
            frame = frame.rename(columns={"index": "timestamp"})
        else:
            return pd.DataFrame()

    rename_map = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    }
    available = {key: value for key, value in rename_map.items() if key in frame.columns}
    frame = frame.rename(columns=available)
    required = {"timestamp", "close", "volume"}
    if not required.issubset(set(frame.columns)):
        return pd.DataFrame()

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").fillna(0)
    frame = frame.dropna(subset=["timestamp", "close"]).sort_values("timestamp").reset_index(drop=True)
    return frame


def _build_state_key(*, alert: RealMarketActivityAlert, now_utc: datetime) -> str:
    bucket_minutes = max(1, int(alert.window_minutes))
    minute_bucket = (now_utc.minute // bucket_minutes) * bucket_minutes
    bucket = now_utc.replace(minute=minute_bucket, second=0, microsecond=0)
    severity_bucket = int(
        abs(alert.price_change_pct) // max(0.1, alert.price_threshold_pct)
    )
    return (
        f"{alert.alert_type}:{alert.pressure}:"
        f"{bucket.strftime('%Y%m%d%H%M')}:"
        f"{severity_bucket}:"
        f"{int(alert.volume_spike_ratio * 10)}"
    )


def _build_alert_message(alert: RealMarketActivityAlert, language: str) -> str:
    """Build English, Traditional Chinese, or bilingual Discord content."""
    is_rise = alert.price_change_pct > 0
    direction_en = "rise" if is_rise else "fall"
    pressure_en = "buying pressure" if is_rise else "selling pressure"
    if alert.alert_type == "sudden_price_move":
        title_en = (
            f"Sudden market price alert: {alert.ticker} has a sharp {direction_en}."
        )
        trigger_en = (
            f"Price moved at least {alert.price_threshold_pct:.2f}% "
            f"within {alert.window_minutes} minutes"
        )
    else:
        title_en = (
            f"Real market activity alert: {alert.ticker} shows unusual {pressure_en}."
        )
        trigger_en = "Price, traded value, and volume spike thresholds were reached"
    english = (
        f"{title_en}\n"
        f"- Window: {alert.window_minutes} minutes\n"
        f"- Price move: {alert.price_change_pct:+.2f}%\n"
        f"- Latest price: {alert.latest_close:,.2f}\n"
        f"- Window volume: {alert.window_volume:,.0f} shares\n"
        f"- Volume versus recent normal: {alert.volume_spike_ratio:.1f}x\n"
        f"- Estimated traded value: {alert.traded_value:,.0f} quote-currency units\n"
        f"- Trigger: {trigger_en}\n"
        "This delayed market-data alert is for simulation and educational monitoring only, "
        "not financial advice."
    )

    direction_zh = "急升" if is_rise else "急跌"
    pressure_zh = "買盤壓力" if is_rise else "賣盤壓力"
    if alert.alert_type == "sudden_price_move":
        title_zh = f"市場價格急變警報：{alert.ticker} 出現{direction_zh}。"
        trigger_zh = (
            f"{alert.window_minutes} 分鐘內的價格變動達 "
            f"{alert.price_threshold_pct:.2f}%"
        )
    else:
        title_zh = f"市場異常活動警報：{alert.ticker} 顯示異常{pressure_zh}。"
        trigger_zh = "價格、估算成交金額及成交量升幅均達到警報門檻"
    traditional_chinese = (
        f"{title_zh}\n"
        f"- 監察時段：{alert.window_minutes} 分鐘\n"
        f"- 價格變動：{alert.price_change_pct:+.2f}%\n"
        f"- 最新價格：{alert.latest_close:,.2f}\n"
        f"- 時段成交量：{alert.window_volume:,.0f} 股\n"
        f"- 相對近期正常成交量：{alert.volume_spike_ratio:.1f} 倍\n"
        f"- 估算成交金額：{alert.traded_value:,.0f} 報價貨幣單位\n"
        f"- 觸發原因：{trigger_zh}\n"
        "此警報使用延遲市場數據，僅供模擬交易、教育及監察用途，並非投資建議。"
    )

    normalized_language = str(language or "en").strip().lower()
    if normalized_language == "zh":
        return traditional_chinese
    if normalized_language == "bilingual":
        return f"{english}\n\n{traditional_chinese}"
    return english


def build_real_market_activity_alert(
    *,
    user_id: str,
    ticker: str,
    intraday_df: pd.DataFrame,
    window_minutes: int,
    large_value_threshold: float,
    volume_spike_multiplier: float,
    price_move_threshold_pct: float,
    min_window_volume: float,
    sudden_move_threshold_pct: float = 10.0,
    language: str = "en",
    now_utc: datetime | None = None,
) -> RealMarketActivityAlert | None:
    """Return an alert when recent candles show unusual directional pressure."""
    symbol = str(ticker or "").strip().upper()
    clean_user_id = str(user_id or "").strip()
    if not symbol or not clean_user_id:
        return None

    frame = _clean_intraday_frame(intraday_df, symbol)
    if len(frame) < 8:
        return None

    window = max(5, int(window_minutes))
    latest_ts = frame.iloc[-1]["timestamp"].to_pydatetime()
    if latest_ts.tzinfo is None:
        latest_ts = latest_ts.replace(tzinfo=UTC)
    else:
        latest_ts = latest_ts.astimezone(UTC)
    observed_at = now_utc or _utc_now()
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    else:
        observed_at = observed_at.astimezone(UTC)
    maximum_age = timedelta(minutes=max(30, window * 2))
    if latest_ts > observed_at + timedelta(minutes=5) or observed_at - latest_ts > maximum_age:
        return None

    window_start = latest_ts - pd.Timedelta(minutes=window)
    recent = frame[frame["timestamp"] >= window_start]
    prior = frame[frame["timestamp"] < window_start].tail(max(8, len(recent) * 4))
    if recent.empty or prior.empty:
        return None

    start_close = float(recent.iloc[0]["close"])
    latest_close = float(recent.iloc[-1]["close"])
    if start_close <= 0 or latest_close <= 0:
        return None

    price_change_pct = ((latest_close / start_close) - 1.0) * 100.0
    window_volume = float(recent["volume"].sum())
    average_window_volume = float(prior["volume"].mean() * max(1, len(recent)))
    volume_spike_ratio = (
        window_volume / average_window_volume
        if average_window_volume > 0
        else 0.0
    )
    traded_value = window_volume * latest_close
    if not math.isfinite(traded_value) or not math.isfinite(volume_spike_ratio):
        return None

    unusual_activity = (
        window_volume >= min_window_volume
        and traded_value >= large_value_threshold
        and volume_spike_ratio >= volume_spike_multiplier
        and abs(price_change_pct) >= price_move_threshold_pct
    )
    sudden_price_move = abs(price_change_pct) >= max(
        0.1,
        float(sudden_move_threshold_pct),
    )
    if not unusual_activity and not sudden_price_move:
        return None

    pressure = "buying_pressure" if price_change_pct > 0 else "selling_pressure"
    alert_type = "sudden_price_move" if sudden_price_move else "unusual_activity"
    placeholder = RealMarketActivityAlert(
        user_id=clean_user_id,
        ticker=symbol,
        alert_type=alert_type,
        pressure=pressure,
        window_minutes=window,
        price_change_pct=price_change_pct,
        latest_close=latest_close,
        window_volume=window_volume,
        average_window_volume=average_window_volume,
        volume_spike_ratio=volume_spike_ratio,
        traded_value=traded_value,
        threshold_value=large_value_threshold,
        price_threshold_pct=(
            float(sudden_move_threshold_pct)
            if sudden_price_move
            else float(price_move_threshold_pct)
        ),
        state_key="",
        message="",
    )
    # Key deduplication to the latest market candle, not wall-clock time. This
    # prevents a closed market's final sharp move from being resent every cycle.
    state_key = _build_state_key(alert=placeholder, now_utc=latest_ts)
    message = _build_alert_message(placeholder, language)
    return RealMarketActivityAlert(
        **{**placeholder.__dict__, "state_key": state_key, "message": message}
    )


def scan_real_market_activity_alerts(
    *,
    user_id: str,
    tickers: list[str],
    download_fn: IntradayDownloadFn | None = None,
) -> list[RealMarketActivityAlert]:
    """Scan tickers and send Discord alerts for unusual real-market activity."""
    settings = get_settings()
    if not settings.real_market_discord_alert_enabled:
        return []

    symbols = _normalize_tickers(tickers)[: settings.real_market_alert_ticker_limit]
    if not symbols:
        return []

    downloader = download_fn or _default_download_intraday
    alerts: list[RealMarketActivityAlert] = []
    store = get_user_profile_store()
    profile = store.get_or_create_profile(user_id)
    language = str(profile.preferred_language or "en")
    for symbol in symbols:
        try:
            raw_df = downloader(symbol, "5d", "5m")
            alert = build_real_market_activity_alert(
                user_id=user_id,
                ticker=symbol,
                intraday_df=raw_df,
                window_minutes=settings.real_market_alert_window_minutes,
                large_value_threshold=settings.real_market_large_value_threshold,
                volume_spike_multiplier=settings.real_market_volume_spike_multiplier,
                price_move_threshold_pct=settings.real_market_price_move_threshold_pct,
                min_window_volume=settings.real_market_min_window_volume,
                sudden_move_threshold_pct=settings.real_market_sudden_move_threshold_pct,
                language=language,
            )
        except Exception as exc:  # pragma: no cover - defensive provider guard
            logger.warning("Real market activity scan skipped ticker=%s error=%s", symbol, exc)
            continue
        if alert is None:
            continue

        should_send = store.should_send_alert(
            alert.user_id,
            alert.ticker,
            f"real_market_{alert.alert_type}_{alert.pressure}",
            alert.state_key,
        )
        if not should_send:
            logger.info(
                "Real market Discord alert suppressed user_id=%s ticker=%s pressure=%s state=%s",
                alert.user_id,
                alert.ticker,
                alert.pressure,
                alert.state_key,
            )
            continue

        if not settings.discord_webhook_url:
            logger.warning(
                "Real market alert detected but DISCORD_WEBHOOK_URL is not configured: %s",
                alert.message,
            )
            alerts.append(alert)
            continue

        try:
            send_discord_webhook_message(settings.discord_webhook_url, alert.message)
            logger.info(
                "Real market Discord alert sent user_id=%s ticker=%s pressure=%s value=%.2f",
                alert.user_id,
                alert.ticker,
                alert.pressure,
                alert.traded_value,
            )
        except Exception as exc:  # pragma: no cover - network availability varies
            logger.warning(
                "Real market Discord alert failed user_id=%s ticker=%s pressure=%s error=%s",
                alert.user_id,
                alert.ticker,
                alert.pressure,
                exc,
            )
        alerts.append(alert)

    return alerts
