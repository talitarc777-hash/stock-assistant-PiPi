"""Automatic Discord alerts for high explainable ticker scores."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import logging
from typing import Callable

import pandas as pd

from app.core.settings import get_settings
from app.services.discord_webhook import send_discord_webhook_message
from app.services.indicators import add_technical_indicators
from app.services.market_data import get_price_history
from app.services.scoring import ScoreBreakdown, score_from_indicators
from app.services.user_profile_service import get_user_profile_store

logger = logging.getLogger(__name__)

PriceHistoryFn = Callable[[str, str], pd.DataFrame]


@dataclass(frozen=True)
class OverallScoreAlert:
    """One Discord-ready high overall-score notification."""

    user_id: str
    ticker: str
    overall_score: int
    threshold: int
    observed_date: str
    trend_score: int
    momentum_score: int
    confirmation_score: int
    risk_penalty: int
    state_key: str
    message: str


def _normalize_tickers(tickers: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for ticker in tickers:
        symbol = str(ticker or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        normalized.append(symbol)
    return normalized


def _normalize_observed_date(value: object) -> str:
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return datetime.now(UTC).date().isoformat()
    return parsed.date().isoformat()


def _build_message(
    *,
    ticker: str,
    score: ScoreBreakdown,
    threshold: int,
    observed_date: str,
    language: str,
) -> str:
    english = (
        f"High overall-score alert: {ticker} reached {score.total_score}/100 "
        f"(your threshold: {threshold}/100).\n"
        f"- Market-data date: {observed_date}\n"
        f"- Trend: {score.trend_score}/40\n"
        f"- Momentum: {score.momentum_score}/25\n"
        f"- Confirmation: {score.confirmation_score}/15\n"
        f"- Risk adjustment: {score.risk_penalty} points (up to -20)\n"
        f"- Screen result: {score.label}; {score.action_summary}\n"
        "This is an explainable screening score, not an 80% profit probability or a "
        "guarantee. It is for simulation and education, not financial advice."
    )
    traditional_chinese = (
        f"整體評分高位提示：{ticker} 達到 {score.total_score}/100 "
        f"（你的門檻：{threshold}/100）。\n"
        f"- 市場數據日期：{observed_date}\n"
        f"- 趨勢：{score.trend_score}/40\n"
        f"- 動能：{score.momentum_score}/25\n"
        f"- 確認因素：{score.confirmation_score}/15\n"
        f"- 風險調整：{score.risk_penalty} 分（最多扣 20 分）\n"
        f"- 篩選結果：{score.label}；{score.action_summary}\n"
        "這是可解釋的篩選評分，不代表有 80% 獲利機會，亦不保證獲利。"
        "內容僅供模擬交易及教育用途，並非投資建議。"
    )

    normalized_language = str(language or "en").strip().lower()
    if normalized_language == "zh":
        return traditional_chinese
    if normalized_language == "bilingual":
        return f"{english}\n\n{traditional_chinese}"
    return english


def build_overall_score_alert(
    *,
    user_id: str,
    ticker: str,
    score: ScoreBreakdown,
    threshold: int,
    observed_date: str | date | datetime,
    language: str = "en",
) -> OverallScoreAlert | None:
    """Build an alert when the score meets the configured high threshold."""
    clean_user_id = str(user_id or "").strip()
    symbol = str(ticker or "").strip().upper()
    clean_threshold = max(0, min(100, int(threshold)))
    if not clean_user_id or not symbol or score.total_score < clean_threshold:
        return None

    clean_date = _normalize_observed_date(observed_date)
    # At most one notification per ticker, threshold, and market-data date. A
    # failed webhook is not recorded, so the next scheduler cycle can retry.
    state_key = f"{clean_date}:{clean_threshold}"
    return OverallScoreAlert(
        user_id=clean_user_id,
        ticker=symbol,
        overall_score=score.total_score,
        threshold=clean_threshold,
        observed_date=clean_date,
        trend_score=score.trend_score,
        momentum_score=score.momentum_score,
        confirmation_score=score.confirmation_score,
        risk_penalty=score.risk_penalty,
        state_key=state_key,
        message=_build_message(
            ticker=symbol,
            score=score,
            threshold=clean_threshold,
            observed_date=clean_date,
            language=language,
        ),
    )


def collect_overall_score_alerts(
    *,
    user_id: str,
    tickers: list[str],
    price_history_fn: PriceHistoryFn | None = None,
) -> list[OverallScoreAlert]:
    """Collect new high-score states without performing external delivery."""
    symbols = _normalize_tickers(tickers)
    if not symbols:
        return []

    store = get_user_profile_store()
    profile = store.get_or_create_profile(user_id)
    if not bool(profile.alert_enabled) or str(profile.preferred_delivery_source) != "discord":
        return []

    history_loader = price_history_fn or get_price_history
    alerts: list[OverallScoreAlert] = []
    for symbol in symbols:
        try:
            indicator_frame = add_technical_indicators(history_loader(symbol, "1y"))
            score = score_from_indicators(indicator_frame)
            alert = build_overall_score_alert(
                user_id=profile.user_id,
                ticker=symbol,
                score=score,
                threshold=profile.alert_threshold_high,
                observed_date=indicator_frame.iloc[-1].get("date"),
                language=str(profile.preferred_language or "en"),
            )
        except Exception as exc:  # pragma: no cover - defensive provider guard
            logger.warning("Overall-score alert scan skipped ticker=%s error=%s", symbol, exc)
            continue
        if alert is not None and store.is_alert_state_new(
            alert.user_id,
            alert.ticker,
            "score_above_threshold_discord",
            alert.state_key,
        ):
            alerts.append(alert)
    return alerts


def scan_overall_score_discord_alerts(
    *,
    user_id: str,
    tickers: list[str],
    price_history_fn: PriceHistoryFn | None = None,
) -> list[OverallScoreAlert]:
    """Score the alert watchlist and deliver new high-score states to Discord."""
    symbols = _normalize_tickers(tickers)
    if not symbols:
        return []

    store = get_user_profile_store()
    profile = store.get_or_create_profile(user_id)
    if not bool(profile.alert_enabled) or str(profile.preferred_delivery_source) != "discord":
        return []

    settings = get_settings()
    if not settings.discord_webhook_url:
        logger.warning(
            "Overall-score Discord scan skipped because DISCORD_WEBHOOK_URL is not configured "
            "user_id=%s",
            user_id,
        )
        return []

    history_loader = price_history_fn or get_price_history
    alerts: list[OverallScoreAlert] = []
    for symbol in symbols:
        try:
            indicator_frame = add_technical_indicators(history_loader(symbol, "1y"))
            score = score_from_indicators(indicator_frame)
            observed_date = indicator_frame.iloc[-1].get("date")
            alert = build_overall_score_alert(
                user_id=profile.user_id,
                ticker=symbol,
                score=score,
                threshold=profile.alert_threshold_high,
                observed_date=observed_date,
                language=str(profile.preferred_language or "en"),
            )
        except Exception as exc:  # pragma: no cover - defensive provider guard
            logger.warning("Overall-score alert scan skipped ticker=%s error=%s", symbol, exc)
            continue
        if alert is None:
            continue

        rule = "score_above_threshold_discord"
        if not store.is_alert_state_new(
            alert.user_id,
            alert.ticker,
            rule,
            alert.state_key,
        ):
            continue

        try:
            send_discord_webhook_message(settings.discord_webhook_url, alert.message)
            store.record_alert_dispatched(
                alert.user_id,
                alert.ticker,
                rule,
                alert.state_key,
            )
            logger.info(
                "Overall-score Discord alert sent user_id=%s ticker=%s score=%d threshold=%d",
                alert.user_id,
                alert.ticker,
                alert.overall_score,
                alert.threshold,
            )
        except Exception as exc:  # pragma: no cover - network availability varies
            logger.warning(
                "Overall-score Discord alert failed user_id=%s ticker=%s error=%s",
                alert.user_id,
                alert.ticker,
                exc,
            )
        alerts.append(alert)

    return alerts
