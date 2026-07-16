"""Live virtual trader mode using latest model output and market data.

Simulation only:
- no broker execution
- no leverage
- no real-money trading
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import math
from pathlib import Path
import sqlite3
from threading import Thread, Lock
from typing import Any

import pandas as pd

from app.core.settings import get_settings
from app.services.account_ledger_service import (
    AccountLedgerError,
    MIN_TRADE_QUANTITY,
    TRADE_ADMIN_FEE_HKD,
    get_trade_admin_fee_usd,
    get_account_ledger_service,
)
from app.services.equity_curve_service import build_live_equity_curve
from app.services.external_market_context_service import build_external_market_context
from app.services.live_market_data_service import get_live_market_snapshot
from app.services.market_data import get_price_history
from app.services.market_regime import assess_market_regime
from app.services.model_feedback_service import get_model_feedback_service
from app.services.model_lifecycle_service import (
    TRADING_MODEL_PERIODS,
    get_model_lifecycle_service,
)
from app.services.model_results import (
    ModelResultsError,
    list_compatible_saved_model_candidates,
    load_trained_model_bundle,
)
from app.services.prediction_explanations import build_prediction_explanation
from app.services.research_pipeline import build_feature_dataset
from app.services.model_training import prepare_stationary_feature_dataset
from app.services.universe_service import get_active_universe
from app.services.user_profile_service import get_user_profile_store

logger = logging.getLogger(__name__)


class LiveVirtualTraderError(Exception):
    """Raised when live virtual trader inputs/state are invalid."""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _normalize_tickers(tickers: list[str]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for ticker in tickers:
        symbol = str(ticker).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        values.append(symbol)
    return values


@dataclass(frozen=True)
class LiveStatus:
    user_id: str
    model_name: str
    generated_at_utc: str
    account: dict[str, Any]
    holdings: list[dict[str, Any]]
    latest_decisions: list[dict[str, Any]]
    contribution_events: list[dict[str, Any]]
    universe_size: int = 0
    tickers_evaluated: int = 0
    tickers_failed: int = 0
    fallback_used_count: int = 0
    equity_curve: list[dict[str, Any]] | None = None


_AUTO_TRAIN_ON_MODEL_MISS = False
_TRAINING_QUEUE: set[tuple[str, str, str]] = set()
_TRAINING_LOCK = Lock()
TRADING_TARGET_NAME = "target_5d_return"
BENCHMARK_SHADOW_TARGET_NAME = "target_5d_outperform"
BENCHMARK_SHADOW_MODEL_NAME = "random_forest"
AUTO_TRADING_MODEL_NAME = "auto_best"
TRADING_MODEL_NAMES = ("linear_regression", "random_forest", "gradient_boosting")
PARTIAL_SELL_FRACTION = 0.5
MIN_CONTEXT_SCORE_FOR_BUY = 55.0
MAX_CONTEXT_SCORE_FOR_HOLD = 35.0
PORTFOLIO_CAUTION_LOSS_PCT = -8.0
PORTFOLIO_BUY_PAUSE_LOSS_PCT = -15.0
PORTFOLIO_REDUCTION_LOSS_PCT = -25.0


def _build_portfolio_risk_state(account: dict[str, Any]) -> dict[str, Any]:
    """Derive account-level safeguards from equity versus net contributed cash."""
    equity = float(account.get("total_account_value") or 0.0)
    net_deposits = float(account.get("net_deposits") or 0.0)
    if net_deposits <= 0:
        return {
            "level": "unavailable",
            "performance_vs_contributions_pct": None,
            "buy_allowed": True,
            "position_size_multiplier": 1.0,
            "reduce_positions": False,
        }

    performance_pct = (equity / net_deposits - 1.0) * 100.0
    if performance_pct <= PORTFOLIO_REDUCTION_LOSS_PCT:
        level, multiplier = "critical", 0.0
    elif performance_pct <= PORTFOLIO_BUY_PAUSE_LOSS_PCT:
        level, multiplier = "paused", 0.0
    elif performance_pct <= PORTFOLIO_CAUTION_LOSS_PCT:
        level, multiplier = "caution", 0.5
    else:
        level, multiplier = "normal", 1.0
    return {
        "level": level,
        "performance_vs_contributions_pct": performance_pct,
        "buy_allowed": multiplier > 0,
        "position_size_multiplier": multiplier,
        "reduce_positions": level == "critical",
    }


def _regression_prediction_confidence(
    *,
    predicted_return_pct: float,
    metrics_summary: dict[str, Any] | None,
    model_reliability: float | None,
) -> dict[str, Any]:
    """Estimate current regression confidence from signal size versus OOS error."""
    payload = dict(metrics_summary or {})
    metrics = dict(payload.get("metrics") or {})
    error_scale = _safe_float(metrics.get("absolute_error_80_pct"))
    error_source = "absolute_error_80_pct"
    if error_scale is None or error_scale <= 0:
        error_scale = _safe_float(metrics.get("rmse"))
        error_source = "rmse"
    if error_scale is None or error_scale <= 0:
        return {
            "confidence_score": None,
            "source": "unavailable",
            "reason": "out_of_sample_error_missing",
        }

    signal_size = abs(float(predicted_return_pct))
    signal_to_noise = signal_size / (signal_size + error_scale)
    reliability = (
        max(0.0, min(1.0, float(model_reliability)))
        if model_reliability is not None
        else 0.5
    )
    confidence = math.sqrt(max(0.0, signal_to_noise * reliability))
    return {
        "confidence_score": confidence,
        "source": "regression_signal_to_noise",
        "predicted_return_pct": float(predicted_return_pct),
        "out_of_sample_error_pct": float(error_scale),
        "error_source": error_source,
        "signal_to_noise": signal_to_noise,
        "model_reliability": reliability,
    }


def _assess_market_data_quality(
    *,
    snapshot: dict[str, Any],
    feature_row: pd.Series,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Validate daily market data before allowing a new simulated position."""
    now = now_utc or datetime.now(UTC)
    reasons: list[str] = []
    values: dict[str, float] = {}
    for field in ("open", "high", "low", "close", "volume"):
        value = _safe_float(snapshot.get(field))
        if value is None or not math.isfinite(value):
            reasons.append(f"{field}_missing_or_invalid")
        else:
            values[field] = value

    if all(field in values for field in ("open", "high", "low", "close")):
        if min(values["open"], values["close"]) < values["low"]:
            reasons.append("ohlc_below_reported_low")
        if max(values["open"], values["close"]) > values["high"]:
            reasons.append("ohlc_above_reported_high")
        if values["low"] <= 0 or values["high"] <= 0:
            reasons.append("non_positive_price")
    if values.get("volume", 0.0) < 0:
        reasons.append("negative_volume")

    price_timestamp = pd.to_datetime(
        snapshot.get("price_timestamp"),
        errors="coerce",
        utc=True,
    )
    business_day_age: int | None = None
    if pd.isna(price_timestamp):
        reasons.append("price_timestamp_missing")
    else:
        price_day = price_timestamp.date()
        now_day = now.date()
        if price_day > now_day:
            reasons.append("price_timestamp_in_future")
        else:
            business_day_age = max(
                0,
                len(pd.bdate_range(
                    start=price_day,
                    end=now_day,
                    inclusive="right",
                )),
            )
            if business_day_age > 2:
                reasons.append("price_older_than_two_business_days")

    feature_timestamp = pd.to_datetime(
        feature_row.get("date"),
        errors="coerce",
        utc=True,
    )
    feature_close = _safe_float(feature_row.get("close"))
    if pd.isna(feature_timestamp):
        reasons.append("feature_timestamp_missing")
    elif not pd.isna(price_timestamp):
        feature_lag = len(pd.bdate_range(
            start=feature_timestamp.date(),
            end=price_timestamp.date(),
            inclusive="right",
        )) if feature_timestamp.date() <= price_timestamp.date() else 0
        if feature_timestamp.date() > price_timestamp.date():
            reasons.append("feature_data_newer_than_price_snapshot")
        elif feature_lag > 2:
            reasons.append("feature_data_too_old")
        elif (
            feature_timestamp.date() == price_timestamp.date()
            and feature_close is not None
            and values.get("close")
            and abs(feature_close / values["close"] - 1.0) > 0.01
        ):
            reasons.append("same_day_close_mismatch")

    return {
        "trade_safe": not reasons,
        "status": "ready" if not reasons else "blocked",
        "reasons": reasons,
        "price_timestamp": (
            price_timestamp.isoformat()
            if not pd.isna(price_timestamp)
            else None
        ),
        "business_day_age": business_day_age,
        "provider_note": snapshot.get("data_freshness_note"),
    }


class LiveVirtualTraderStore:
    """SQLite persistence for live positions and action log."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = Path(db_path or get_settings().profile_db_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_trader_positions (
                    user_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    avg_entry_price REAL NOT NULL,
                    entry_timestamp TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, ticker)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_trader_trade_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    action TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    model_name TEXT NOT NULL,
                    confidence_score REAL,
                    reason TEXT NOT NULL,
                    threshold_summary TEXT NOT NULL,
                    technical_state_summary TEXT NOT NULL,
                    news_sentiment_summary TEXT NOT NULL,
                    benchmark_strength_summary TEXT NOT NULL,
                    action_summary TEXT NOT NULL,
                    cash_after REAL NOT NULL,
                    holdings_after REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def list_positions(self, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM live_trader_positions
                WHERE user_id = ?
                ORDER BY ticker ASC
                """,
                (str(user_id).strip(),),
            ).fetchall()
        return [
            {
                "user_id": row["user_id"],
                "ticker": row["ticker"],
                "quantity": float(row["quantity"]),
                "avg_entry_price": float(row["avg_entry_price"]),
                "entry_timestamp": row["entry_timestamp"],
                "model_name": row["model_name"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get_position(self, user_id: str, ticker: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM live_trader_positions
                WHERE user_id = ? AND ticker = ?
                """,
                (str(user_id).strip(), str(ticker).strip().upper()),
            ).fetchone()
        if row is None:
            return None
        return {
            "user_id": row["user_id"],
            "ticker": row["ticker"],
            "quantity": float(row["quantity"]),
            "avg_entry_price": float(row["avg_entry_price"]),
            "entry_timestamp": row["entry_timestamp"],
            "model_name": row["model_name"],
            "updated_at": row["updated_at"],
        }

    def upsert_position(
        self,
        user_id: str,
        ticker: str,
        quantity: float,
        avg_entry_price: float,
        model_name: str,
        entry_timestamp: str | None = None,
    ) -> None:
        now = _utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT entry_timestamp FROM live_trader_positions WHERE user_id = ? AND ticker = ?",
                (str(user_id).strip(), str(ticker).strip().upper()),
            ).fetchone()
            first_entry = entry_timestamp or (existing["entry_timestamp"] if existing else now)
            conn.execute(
                """
                INSERT INTO live_trader_positions (
                    user_id, ticker, quantity, avg_entry_price, entry_timestamp, model_name, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, ticker) DO UPDATE SET
                    quantity = excluded.quantity,
                    avg_entry_price = excluded.avg_entry_price,
                    model_name = excluded.model_name,
                    updated_at = excluded.updated_at
                """,
                (
                    str(user_id).strip(),
                    str(ticker).strip().upper(),
                    float(quantity),
                    float(avg_entry_price),
                    first_entry,
                    str(model_name).strip().lower(),
                    now,
                ),
            )
            conn.commit()

    def remove_position(self, user_id: str, ticker: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM live_trader_positions WHERE user_id = ? AND ticker = ?",
                (str(user_id).strip(), str(ticker).strip().upper()),
            )
            conn.commit()

    def append_trade(self, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO live_trader_trade_log (
                    timestamp, user_id, ticker, action, quantity, price, model_name,
                    confidence_score, reason, threshold_summary, technical_state_summary,
                    news_sentiment_summary, benchmark_strength_summary, action_summary,
                    cash_after, holdings_after, realized_pnl, unrealized_pnl, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["timestamp"],
                    payload["user_id"],
                    payload["ticker"],
                    payload["action"],
                    float(payload["quantity"]),
                    float(payload["price"]),
                    payload["model_name"],
                    payload.get("confidence_score"),
                    payload["reason"],
                    payload["threshold_summary"],
                    payload["technical_state_summary"],
                    payload["news_sentiment_summary"],
                    payload["benchmark_strength_summary"],
                    payload["action_summary"],
                    float(payload["cash_after"]),
                    float(payload["holdings_after"]),
                    float(payload["realized_pnl"]),
                    float(payload["unrealized_pnl"]),
                    json.dumps(payload.get("metadata", {}), ensure_ascii=False),
                ),
            )
            conn.commit()

    def list_trades(self, user_id: str, limit: int = 50, ticker: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM live_trader_trade_log WHERE user_id = ?"
        params: list[Any] = [str(user_id).strip()]
        if ticker:
            sql += " AND ticker = ?"
            params.append(str(ticker).strip().upper())
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            result.append(
                {
                    "timestamp": row["timestamp"],
                    "user_id": row["user_id"],
                    "ticker": row["ticker"],
                    "action": row["action"],
                    "quantity": float(row["quantity"]),
                    "price": float(row["price"]),
                    "model_name": row["model_name"],
                    "confidence_score": row["confidence_score"],
                    "reason": row["reason"],
                    "threshold_summary": row["threshold_summary"],
                    "technical_state_summary": row["technical_state_summary"],
                    "news_sentiment_summary": row["news_sentiment_summary"],
                    "benchmark_strength_summary": row["benchmark_strength_summary"],
                    "action_summary": row["action_summary"],
                    "cash_after": float(row["cash_after"]),
                    "holdings_after": float(row["holdings_after"]),
                    "realized_pnl": float(row["realized_pnl"]),
                    "unrealized_pnl": float(row["unrealized_pnl"]),
                    "metadata": metadata,
                }
            )
        return result


def _resolve_user_tickers(user_id: str, tickers: list[str] | None) -> list[str]:
    if tickers:
        values = _normalize_tickers(tickers)
        if values:
            return values
    # Autonomous mode default: scan a broader active universe.
    values = _normalize_tickers(get_active_universe(limit=120))
    # Keep user watchlist symbols included for continuity.
    watchlist, _, _ = get_user_profile_store().get_effective_watchlist(user_id=user_id)
    values = _normalize_tickers(values + watchlist)
    if not values:
        raise LiveVirtualTraderError("No tickers available for live virtual trader.")
    return values


def _confidence_ok(confidence_score: float | None, threshold: float) -> bool:
    return (
        confidence_score is not None
        and float(confidence_score) >= float(threshold)
    )


def _derive_signal_flags(predicted_value: float, task_type: str, min_return: float) -> tuple[bool, bool]:
    if task_type == "classification":
        bullish = int(round(predicted_value)) == 1
        bearish = int(round(predicted_value)) == 0
    else:
        bullish = predicted_value > min_return
        bearish = predicted_value < 0.0
    return bullish, bearish


def _latest_prices_for_symbols(symbols: list[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for symbol in symbols:
        try:
            history = get_price_history(symbol, period="3mo")
            prices[symbol] = float(history.sort_values("date").iloc[-1]["close"])
        except Exception as exc:
            logger.warning("Live trader latest price skipped ticker=%s error=%s", symbol, exc)
    return prices


def _schedule_background_training_if_enabled(
    *,
    ticker: str,
    period: str,
    benchmark: str,
) -> None:
    """Optionally schedule non-blocking ticker training when model is missing."""
    if not _AUTO_TRAIN_ON_MODEL_MISS:
        return

    job_key = (ticker, period, benchmark)
    with _TRAINING_LOCK:
        if job_key in _TRAINING_QUEUE:
            return
        _TRAINING_QUEUE.add(job_key)

    def _worker() -> None:
        try:
            from app.services.model_training import train_baseline_models_for_ticker

            train_baseline_models_for_ticker(
                ticker=ticker,
                period=period,
                benchmark=benchmark,
                include_gradient_boosting=True,
            )
            logger.info("Background training completed ticker=%s period=%s", ticker, period)
        except Exception as exc:  # pragma: no cover - background defensive guard
            logger.exception("Background training failed ticker=%s error=%s", ticker, exc)
        finally:
            with _TRAINING_LOCK:
                _TRAINING_QUEUE.discard(job_key)

    Thread(target=_worker, name=f"train-{ticker}-{period}", daemon=True).start()


def _build_rule_based_fallback(
    latest_row: pd.Series,
    min_predicted_return_pct: float,
) -> tuple[float, float, str, str]:
    """Return fallback prediction tuple: predicted_value, confidence, task_type, reason."""
    close = float(latest_row.get("close", 0.0) or 0.0)
    sma50 = float(latest_row.get("sma_50", 0.0) or 0.0)
    sma200 = float(latest_row.get("sma_200", 0.0) or 0.0)
    rsi = float(latest_row.get("rsi_14", 0.0) or 0.0)
    macd_line = float(latest_row.get("macd_line", 0.0) or 0.0)
    macd_signal = float(latest_row.get("macd_signal", 0.0) or 0.0)

    bullish_trend = close > sma50 > sma200 if sma50 > 0 and sma200 > 0 else False
    positive_momentum = (50.0 <= rsi <= 70.0) and (macd_line >= macd_signal)
    bearish_trend = close < sma50 < sma200 if sma50 > 0 and sma200 > 0 else False

    if bullish_trend and positive_momentum:
        reason = "fallback_rule_bullish_trend_momentum"
        return (max(1.0, float(min_predicted_return_pct)), 0.65, "regression", reason)
    if bearish_trend and rsi < 45.0:
        reason = "fallback_rule_bearish_trend"
        return (-1.0, 0.60, "regression", reason)
    reason = "fallback_rule_neutral_hold"
    return (0.0, 0.55, "regression", reason)


def _safe_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _score_virtual_trader_context(
    *,
    latest_row: pd.Series,
    snapshot: dict[str, Any],
    external_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score non-price context used to confirm virtual-trader actions.

    This is intentionally transparent and conservative. It uses data already
    available to the trader run: news sentiment, benchmark strength, valuation,
    company size, volatility, technical alignment, and headline-derived context
    for political/regulatory risk, public interest, analyst revisions, and
    earnings tone.
    """
    score = 50.0
    factors: list[str] = []

    recent_articles = _safe_float(latest_row.get("article_count_recent_7d"))
    recent_sentiment = _safe_float(latest_row.get("average_sentiment_recent_7d"))
    positive_ratio = _safe_float(latest_row.get("positive_article_ratio_recent_7d"))
    negative_ratio = _safe_float(latest_row.get("negative_article_ratio_recent_7d"))
    if recent_articles and recent_articles > 0 and recent_sentiment is not None:
        if recent_sentiment >= 0.15 or (positive_ratio or 0.0) > (negative_ratio or 0.0) + 0.2:
            score += 10
            factors.append("recent news tone supports the setup")
        elif recent_sentiment <= -0.15 or (negative_ratio or 0.0) > (positive_ratio or 0.0) + 0.2:
            score -= 12
            factors.append("recent news tone is a risk")
        else:
            factors.append("recent news tone is balanced")
    else:
        factors.append("news/public sentiment data is limited")

    political_risk_ratio = _safe_float(latest_row.get("political_risk_article_ratio_recent_7d"))
    if political_risk_ratio is not None:
        if political_risk_ratio >= 0.35:
            score -= 10
            factors.append("headline political/regulatory risk is elevated")
        elif political_risk_ratio >= 0.15:
            score -= 5
            factors.append("some political/regulatory risk appears in headlines")

    public_interest_ratio = _safe_float(latest_row.get("public_interest_article_ratio_recent_7d"))
    if public_interest_ratio is not None and public_interest_ratio >= 0.3:
        if recent_sentiment is not None and recent_sentiment < -0.1:
            score -= 5
            factors.append("public/social attention is negative")
        else:
            score += 6
            factors.append("public/social attention supports the setup")

    analyst_positive_ratio = _safe_float(latest_row.get("analyst_positive_ratio_recent_7d"))
    analyst_negative_ratio = _safe_float(latest_row.get("analyst_negative_ratio_recent_7d"))
    analyst_positive_ratio = analyst_positive_ratio or 0.0
    analyst_negative_ratio = analyst_negative_ratio or 0.0
    if analyst_positive_ratio > analyst_negative_ratio + 0.1:
        score += 7
        factors.append("headline analyst revisions are positive")
    elif analyst_negative_ratio > analyst_positive_ratio + 0.1:
        score -= 9
        factors.append("headline analyst revisions are negative")

    earnings_positive_ratio = _safe_float(latest_row.get("earnings_positive_ratio_recent_7d"))
    earnings_negative_ratio = _safe_float(latest_row.get("earnings_negative_ratio_recent_7d"))
    earnings_positive_ratio = earnings_positive_ratio or 0.0
    earnings_negative_ratio = earnings_negative_ratio or 0.0
    if earnings_positive_ratio > earnings_negative_ratio + 0.1:
        score += 7
        factors.append("headline earnings tone is positive")
    elif earnings_negative_ratio > earnings_positive_ratio + 0.1:
        score -= 10
        factors.append("headline earnings tone is negative")

    external_context = external_context or {}
    social_sentiment = _safe_float(external_context.get("social_sentiment_score"))
    social_mentions = _safe_float(external_context.get("social_mention_count"))
    social_engagement = _safe_float(external_context.get("social_engagement_score"))
    if social_mentions and social_mentions > 0 and social_sentiment is not None:
        if social_sentiment >= 0.12:
            score += 6
            factors.append("direct public-opinion feed is positive")
        elif social_sentiment <= -0.12:
            score -= 7
            factors.append("direct public-opinion feed is negative")
        else:
            factors.append("direct public-opinion feed is balanced")
        if social_engagement is not None and social_engagement >= 50 and social_sentiment < 0:
            score -= 3
            factors.append("negative public discussion has high engagement")

    analyst_revision_score = _safe_float(external_context.get("analyst_revision_score"))
    analyst_consensus_score = _safe_float(external_context.get("analyst_consensus_score"))
    analyst_score = analyst_revision_score if analyst_revision_score is not None else analyst_consensus_score
    if analyst_score is not None:
        if analyst_score >= 0.25:
            score += 8
            factors.append("analyst consensus/revisions support the setup")
        elif analyst_score <= -0.25:
            score -= 10
            factors.append("analyst consensus/revisions are a risk")

    regulatory_risk = _safe_float(external_context.get("official_regulatory_risk_score"))
    if regulatory_risk is not None:
        if regulatory_risk >= 50:
            score -= 10
            factors.append("official regulatory filings show elevated event risk")
        elif regulatory_risk >= 20:
            score -= 5
            factors.append("official regulatory filings show some event risk")

    earnings_call_tone = _safe_float(external_context.get("earnings_call_tone_score"))
    if earnings_call_tone is not None:
        if earnings_call_tone >= 0.12:
            score += 6
            factors.append("earnings-call transcript tone is positive")
        elif earnings_call_tone <= -0.12:
            score -= 8
            factors.append("earnings-call transcript tone is negative")

    benchmark_score = _safe_float(latest_row.get("benchmark_strength_score"))
    if benchmark_score is not None:
        if benchmark_score >= 75:
            score += 8
            factors.append("ticker has strong relative performance versus benchmark")
        elif benchmark_score <= 25:
            score -= 8
            factors.append("ticker is weak versus benchmark")

    close = _safe_float(latest_row.get("close"))
    sma50 = _safe_float(latest_row.get("sma_50"))
    sma200 = _safe_float(latest_row.get("sma_200"))
    if close and sma50 and sma200:
        if close > sma50 > sma200:
            score += 8
            factors.append("technical trend is constructive")
        elif close < sma50 < sma200:
            score -= 10
            factors.append("technical trend is weak")

    volatility = _safe_float(latest_row.get("rolling_volatility_20_pct"))
    if volatility is not None:
        if volatility <= 25:
            score += 4
            factors.append("recent volatility is controlled")
        elif volatility >= 55:
            score -= 10
            factors.append("recent volatility is high")

    pe_ratio = _safe_float(snapshot.get("pe_ratio"))
    if pe_ratio is not None:
        if 0 < pe_ratio <= 35:
            score += 5
            factors.append("valuation is not extreme by PE")
        elif pe_ratio > 85:
            score -= 15
            factors.append("PE valuation is very high")
        elif pe_ratio > 50:
            score -= 7
            factors.append("PE valuation is elevated")
    else:
        factors.append("PE valuation is unavailable")

    market_cap = _safe_float(snapshot.get("market_cap"))
    if market_cap is not None:
        if market_cap >= 10_000_000_000:
            score += 5
            factors.append("company size/liquidity looks established")
        elif market_cap < 1_000_000_000:
            score -= 8
            factors.append("company size is small, adding risk")
    else:
        factors.append("company size is unavailable")

    if snapshot.get("sector") or snapshot.get("industry"):
        score += 2
        factors.append("sector/industry context is available")
    else:
        factors.append("sector/social trend context is limited")

    score = max(0.0, min(100.0, score))
    return {
        "score": score,
        "label": "supportive" if score >= MIN_CONTEXT_SCORE_FOR_BUY else "cautious" if score >= 40 else "weak",
        "factors": factors,
        "summary": f"Context score {score:.0f}/100: " + "; ".join(factors[:4]) + ".",
        "missing_context": [
            *external_context.get("missing_sources", []),
        ],
    }


def _calculate_sell_quantity(holding_quantity: float, reason: str) -> int:
    """Size exits in whole shares while allowing partial position reduction."""
    whole_holding = max(0, math.floor(float(holding_quantity)))
    if whole_holding <= 1 or reason == "stop_loss":
        return whole_holding
    return max(1, math.ceil(whole_holding * PARTIAL_SELL_FRACTION))


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    """Parse ISO timestamp safely for cooldown checks."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_trade_cooldown_active(
    *,
    latest_trade: dict[str, Any] | None,
    action: str,
    now_utc: datetime,
    cooldown_minutes: float,
) -> bool:
    """Return True when the same action was already executed within cooldown."""
    if cooldown_minutes <= 0:
        return False
    if not latest_trade:
        return False
    latest_action = str(latest_trade.get("action", "")).lower()
    if latest_action != str(action).lower():
        return False
    latest_ts = _parse_iso_timestamp(latest_trade.get("timestamp"))
    if latest_ts is None:
        return False
    elapsed_seconds = (now_utc - latest_ts).total_seconds()
    if elapsed_seconds < 0:
        return False
    return elapsed_seconds < (float(cooldown_minutes) * 60.0)


def _build_model_inference_frame(
    *,
    latest_row: pd.Series,
    stationary_latest_row: pd.Series | None,
    candidate_ticker: str,
    feature_names: list[str],
    stationary_features: bool = False,
) -> pd.DataFrame:
    """Use the training-time feature representation for one live prediction."""
    inference_row = (
        stationary_latest_row
        if stationary_features
        and stationary_latest_row is not None
        else latest_row
    )
    return pd.DataFrame(
        [{name: inference_row.get(name, None) for name in feature_names}]
    )


def _build_benchmark_shadow_prediction(
    *,
    ticker: str,
    latest_row: pd.Series,
    stationary_latest_row: pd.Series | None,
    benchmark: str,
) -> dict[str, Any]:
    """Evaluate a validated benchmark-relative model without affecting trades.

    Shadow inference is deliberately exact-ticker only. A model trained for one
    company or ETF must not silently control another symbol, and a research
    candidate cannot become executable merely because an artifact exists.
    """
    clean_ticker = str(ticker).strip().upper()
    clean_benchmark = str(benchmark).strip().upper()
    if clean_ticker == clean_benchmark:
        return {
            "status": "not_applicable",
            "reason": "ticker_is_benchmark",
            "execution_enabled": False,
        }

    errors: list[str] = []
    for candidate_period in ("10y", "5y", "2y"):
        try:
            bundle = load_trained_model_bundle(
                ticker=clean_ticker,
                period=candidate_period,
                target_name=BENCHMARK_SHADOW_TARGET_NAME,
                model_name=BENCHMARK_SHADOW_MODEL_NAME,
            )
        except ModelResultsError as exc:
            errors.append(str(exc))
            continue

        summary = dict(bundle.get("metrics") or {})
        economics = dict(summary.get("outperformance_economics_gate") or {})
        provenance_current = (
            int(summary.get("validation_scheme_version") or 0) >= 4
            and int(summary.get("feature_schema_version") or 0) >= 2
            and bool(summary.get("stationary_features"))
            and bool(summary.get("benchmark_relative_target"))
            and bool(economics.get("passed"))
            and bool(economics.get("regime_filter_applied"))
            and bool(economics.get("position_multiplier_applied"))
        )
        if not provenance_current:
            return {
                "status": "rejected",
                "reason": "validation_or_economics_evidence_incomplete",
                "model_name": BENCHMARK_SHADOW_MODEL_NAME,
                "model_period": candidate_period,
                "execution_enabled": False,
            }

        feature_names = list(bundle.get("feature_names") or [])
        x_latest = _build_model_inference_frame(
            latest_row=latest_row,
            stationary_latest_row=stationary_latest_row,
            candidate_ticker=clean_ticker,
            feature_names=feature_names,
            stationary_features=True,
        )
        model = bundle["model"]
        prediction = int(model.predict(x_latest)[0])
        probability = None
        if hasattr(model, "predict_proba"):
            probabilities = model.predict_proba(x_latest)[0]
            classes = list(getattr(model, "classes_", []))
            if 1 in classes:
                probability = float(probabilities[classes.index(1)])
        metrics = dict(summary.get("metrics") or {})
        return {
            "status": "available",
            "execution_enabled": False,
            "target": BENCHMARK_SHADOW_TARGET_NAME,
            "benchmark": clean_benchmark,
            "model_name": BENCHMARK_SHADOW_MODEL_NAME,
            "model_period": candidate_period,
            "prediction": prediction,
            "signal": "outperform" if prediction == 1 else "not_outperform",
            "outperform_probability": probability,
            "validation_accuracy": _safe_float(metrics.get("accuracy")),
            "economics_passed": True,
            "average_net_signal_return_pct": _safe_float(
                economics.get("average_net_stock_return_pct")
            ),
            "profitable_path_rate": _safe_float(
                economics.get("profitable_non_overlapping_path_rate")
            ),
            "worst_path_drawdown_pct": _safe_float(
                economics.get("worst_path_drawdown_pct")
            ),
            "beginner_note": (
                "Research comparison only. This does not place a trade or guarantee profit."
            ),
        }

    return {
        "status": "unavailable",
        "reason": "exact_ticker_validated_artifact_missing",
        "execution_enabled": False,
        "attempted_periods": ["10y", "5y", "2y"],
        "error_count": len(errors),
    }


def collect_benchmark_shadow_observation(
    *,
    ticker: str,
    benchmark: str = "VOO",
    feature_period: str = "2y",
) -> dict[str, Any]:
    """Collect one system-owned shadow sample without touching any account.

    The feedback table deduplicates ticker/model/market-date observations, so
    repeated scheduler cycles cannot inflate forward evidence.
    """
    symbol = str(ticker).strip().upper()
    benchmark_symbol = str(benchmark).strip().upper()
    feature_df = build_feature_dataset(
        ticker=symbol,
        period=feature_period,
        benchmark=benchmark_symbol,
        include_news_sentiment=False,
        sentiment_model="finbert",
    ).sort_values("date")
    if feature_df.empty:
        return {
            "ticker": symbol,
            "recorded": False,
            "reason": "feature_dataset_empty",
        }
    latest_row = feature_df.iloc[-1]
    stationary_row = prepare_stationary_feature_dataset(feature_df).iloc[-1]
    shadow = _build_benchmark_shadow_prediction(
        ticker=symbol,
        latest_row=latest_row,
        stationary_latest_row=stationary_row,
        benchmark=benchmark_symbol,
    )
    if shadow.get("status") != "available":
        return {
            "ticker": symbol,
            "recorded": False,
            "reason": shadow.get("reason") or shadow.get("status"),
        }
    payload = {
        "timestamp": _utc_now(),
        "user_id": "system-shadow-collector",
        "ticker": symbol,
        "action": "no_action",
        "quantity": 0.0,
        "price": float(latest_row["close"]),
        "model_name": str(shadow["model_name"]),
        "metadata": {
            "price_date": str(latest_row["date"]),
            "benchmark_shadow": shadow,
        },
    }
    recorded = get_model_feedback_service().record_benchmark_shadow(payload)
    return {
        "ticker": symbol,
        "market_date": str(pd.to_datetime(latest_row["date"]).date()),
        "recorded": bool(recorded),
        "reason": "recorded" if recorded else "already_recorded_for_market_date",
        "shadow": shadow,
    }


def run_live_virtual_trader_now(
    user_id: str,
    tickers: list[str] | None = None,
    model_name: str | None = AUTO_TRADING_MODEL_NAME,
    period: str = "2y",
    benchmark: str = "VOO",
    target_name: str = TRADING_TARGET_NAME,
    confidence_threshold: float = 0.55,
    max_position_size_pct: float = 0.25,
    stop_loss_pct: float = 0.10,
    take_profit_pct: float | None = None,
    min_predicted_return_pct: float = 0.0,
    signal_cooldown_minutes: float = 60.0,
) -> LiveStatus:
    """Run one live decision cycle and persist simulated actions."""
    clean_user_id = str(user_id).strip()
    if not clean_user_id:
        raise LiveVirtualTraderError("user_id is required.")
    if not 0 < max_position_size_pct <= 1:
        raise LiveVirtualTraderError("max_position_size_pct must be within (0, 1].")

    symbols = _resolve_user_tickers(clean_user_id, tickers)
    lifecycle_service = get_model_lifecycle_service()
    ledger = get_account_ledger_service()
    store = get_live_virtual_trader_store()
    contribution_events = ledger.list_events(
        clean_user_id,
        limit=24,
        event_types=["monthly_contribution", "manual_deposit", "withdrawal"],
    )

    decisions: list[dict[str, Any]] = []
    latest_row_cache: dict[str, pd.Series] = {}
    stationary_latest_row_cache: dict[str, pd.Series] = {}
    latest_price_cache: dict[str, float] = {}
    valuation_cache: dict[str, dict[str, Any]] = {}
    data_quality_cache: dict[str, dict[str, Any]] = {}
    failed_symbols: list[str] = []
    fallback_used_count = 0
    requested_model_name = str(model_name or "").strip().lower()
    auto_model_selection = requested_model_name in {"", "auto", AUTO_TRADING_MODEL_NAME}

    for symbol in symbols:
        try:
            feature_df = build_feature_dataset(
                ticker=symbol,
                period=period,
                benchmark=benchmark,
                include_news_sentiment=True,
                sentiment_model="finbert",
            ).sort_values("date")
            if feature_df.empty:
                failed_symbols.append(symbol)
                logger.warning("Live trader ticker=%s skipped because feature dataset is empty", symbol)
                continue
            latest_row = feature_df.iloc[-1]
            latest_row_cache[symbol] = latest_row
            stationary_latest_row_cache[symbol] = prepare_stationary_feature_dataset(
                feature_df
            ).iloc[-1]
            latest_price_cache[symbol] = float(latest_row["close"])
            snapshot = get_live_market_snapshot(symbol, period="3mo")
            valuation_cache[symbol] = snapshot
            latest_price_cache[symbol] = float(snapshot["close"])
            data_quality_cache[symbol] = _assess_market_data_quality(
                snapshot=snapshot,
                feature_row=latest_row,
            )
        except Exception as exc:
            failed_symbols.append(symbol)
            logger.warning("Live trader ticker=%s skipped due to data error: %s", symbol, exc)
            continue

    account = ledger.build_account_summary(clean_user_id, latest_prices=latest_price_cache)
    held_positions = account["holdings"]
    extra_symbols = [item["ticker"] for item in held_positions if item["ticker"] not in latest_price_cache]
    if extra_symbols:
        latest_price_cache.update(_latest_prices_for_symbols(extra_symbols))
        account = ledger.build_account_summary(clean_user_id, latest_prices=latest_price_cache)

    holdings_by_ticker = {item["ticker"]: item for item in account["holdings"]}
    portfolio_risk = _build_portfolio_risk_state(account)

    for symbol in symbols:
        latest_row = latest_row_cache.get(symbol)
        if latest_row is None:
            continue
        current_price = float(latest_price_cache[symbol])
        snapshot = valuation_cache.get(symbol, {})
        data_quality = data_quality_cache.get(
            symbol,
            {
                "trade_safe": False,
                "status": "blocked",
                "reasons": ["data_quality_not_evaluated"],
            },
        )
        market_regime = assess_market_regime(latest_row)
        pe_ratio = snapshot.get("pe_ratio")
        try:
            task_type = "regression"
            prediction_value = 0.0
            confidence_score: float | None = None
            decision_model_name = AUTO_TRADING_MODEL_NAME if auto_model_selection else requested_model_name
            decision_source = "fallback_rule"
            model_fallback_reason = "fallback_rule_neutral_hold"
            model_loaded = False
            model_load_errors: list[str] = []

            registry_candidates = lifecycle_service.resolve_runtime_model_candidates(
                ticker=symbol,
                period=period,
                target_name=target_name,
                requested_model_name=None if auto_model_selection else requested_model_name,
                periods=TRADING_MODEL_PERIODS if auto_model_selection else (period,),
            )
            candidate_periods = TRADING_MODEL_PERIODS if auto_model_selection else (period,)
            saved_candidates: list[dict[str, Any]] = []
            if not auto_model_selection:
                for candidate_period in candidate_periods:
                    for candidate in list_compatible_saved_model_candidates(
                        ticker=symbol,
                        period=candidate_period,
                        target_name=target_name,
                        requested_model_name=requested_model_name,
                        limit=12,
                    ):
                        saved_candidates.append({**candidate, "period": candidate_period})

            runtime_candidates: list[dict[str, Any]] = []
            seen_runtime: set[tuple[str, str, str]] = set()
            for candidate in registry_candidates + saved_candidates:
                tkr = str(candidate.get("ticker", symbol)).strip().upper()
                candidate_period = str(candidate.get("period", period)).strip()
                mdl = str(candidate.get("model_name", "")).strip().lower()
                if auto_model_selection and mdl not in TRADING_MODEL_NAMES:
                    continue
                key = (tkr, candidate_period, mdl)
                if key in seen_runtime:
                    continue
                seen_runtime.add(key)
                runtime_candidates.append(
                    {
                        "ticker": tkr,
                        "period": candidate_period,
                        "model_name": mdl,
                        "source": str(candidate.get("source", "candidate")),
                        "validation_score": candidate.get("validation_score"),
                        "runtime_score": candidate.get("runtime_score"),
                        "feedback_summary": candidate.get("feedback_summary") or {},
                        "model_version": candidate.get("model_version"),
                    }
                )

            if not runtime_candidates and not auto_model_selection:
                fallback_model_names = (requested_model_name,)
                runtime_candidates = [
                    {
                        "ticker": candidate_ticker,
                        "period": candidate_period,
                        "model_name": candidate_model_name,
                        "source": source,
                        "validation_score": None,
                    }
                    for candidate_ticker, source in ((symbol, "trained_model"), ("GLOBAL", "global_model"))
                    for candidate_period in candidate_periods
                    for candidate_model_name in fallback_model_names
                    if candidate_model_name
                ]
            logger.info(
                "Live trader model candidate order ticker=%s candidates=%s",
                symbol,
                [
                    f"{row['ticker']}/{row['period']}/{row['model_name']}:{row['source']}"
                    for row in runtime_candidates[:12]
                ],
            )

            decision_model_period = period
            decision_validation_score: float | None = None
            decision_runtime_score: float | None = None
            decision_model_version = "fallback"
            decision_feedback_summary: dict[str, Any] = {}
            decision_uncertainty: dict[str, Any] = {
                "confidence_score": None,
                "source": "unavailable",
            }
            for candidate in runtime_candidates:
                candidate_ticker = str(candidate.get("ticker", symbol)).strip().upper()
                candidate_period = str(candidate.get("period", period)).strip()
                candidate_model_name = str(candidate.get("model_name", "")).strip().lower()
                source_name = str(candidate.get("source", "trained_model"))
                try:
                    bundle = load_trained_model_bundle(
                        ticker=candidate_ticker,
                        period=candidate_period,
                        target_name=target_name,
                        model_name=candidate_model_name,
                    )
                    model = bundle["model"]
                    feature_names = list(bundle["feature_names"])
                    task_type = str(bundle["task_type"]).lower()
                    x_latest = _build_model_inference_frame(
                        latest_row=latest_row,
                        stationary_latest_row=stationary_latest_row_cache.get(symbol),
                        candidate_ticker=candidate_ticker,
                        feature_names=feature_names,
                        stationary_features=bool(
                            dict(bundle.get("metrics") or {}).get("stationary_features")
                            or dict(bundle.get("metrics") or {}).get("pooled_stationary_features")
                        ),
                    )
                    prediction_value = float(model.predict(x_latest)[0])
                    if hasattr(model, "predict_proba"):
                        try:
                            probs = model.predict_proba(x_latest)
                            confidence_score = float(max(probs[0]))
                        except Exception:
                            confidence_score = None
                    decision_source = source_name
                    decision_model_name = str(bundle.get("model_name", candidate_model_name))
                    decision_model_period = candidate_period
                    score_value = candidate.get("validation_score")
                    decision_validation_score = (
                        float(score_value) if isinstance(score_value, (int, float)) else None
                    )
                    runtime_score = candidate.get("runtime_score")
                    decision_runtime_score = (
                        float(runtime_score)
                        if isinstance(runtime_score, (int, float))
                        else decision_validation_score
                    )
                    decision_model_version = str(
                        candidate.get("model_version") or "legacy"
                    )
                    decision_feedback_summary = dict(
                        candidate.get("feedback_summary") or {}
                    )
                    if task_type == "regression":
                        decision_uncertainty = _regression_prediction_confidence(
                            predicted_return_pct=prediction_value,
                            metrics_summary=dict(bundle.get("metrics") or {}),
                            model_reliability=decision_runtime_score,
                        )
                        confidence_score = decision_uncertainty[
                            "confidence_score"
                        ]
                    else:
                        decision_uncertainty = {
                            "confidence_score": confidence_score,
                            "source": "classifier_predict_proba",
                        }
                    model_loaded = True
                    logger.info(
                        "Live trader selected saved model ticker=%s selected=%s/%s/%s score=%s source=%s",
                        symbol,
                        candidate_ticker,
                        candidate_period,
                        decision_model_name,
                        decision_validation_score,
                        decision_source,
                    )
                    break
                except ModelResultsError as exc:
                    model_load_errors.append(
                        f"{candidate_ticker}/{candidate_period}/{candidate_model_name}:{exc}"
                    )

            if not model_loaded:
                prediction_value, confidence_score, task_type, model_fallback_reason = _build_rule_based_fallback(
                    latest_row,
                    min_predicted_return_pct=min_predicted_return_pct,
                )
                fallback_used_count += 1
                logger.info(
                    "Live trader ticker=%s model_missing -> fallback_strategy used (%s)",
                    symbol,
                    model_fallback_reason,
                )
                _schedule_background_training_if_enabled(
                    ticker=symbol,
                    period=period,
                    benchmark=benchmark,
                )
                logger.info(
                    "Model missing -> using fallback%s",
                    " -> training scheduled" if _AUTO_TRAIN_ON_MODEL_MISS else "",
                )

            benchmark_shadow = _build_benchmark_shadow_prediction(
                ticker=symbol,
                latest_row=latest_row,
                stationary_latest_row=stationary_latest_row_cache.get(symbol),
                benchmark=benchmark,
            )
            if benchmark_shadow.get("status") == "available":
                benchmark_shadow["forward_evidence"] = (
                    lifecycle_service.get_benchmark_forward_promotion_gate(
                        ticker=symbol,
                        period=str(benchmark_shadow["model_period"]),
                        model_name=str(benchmark_shadow["model_name"]),
                    )
                )

            explanation = build_prediction_explanation(
                feature_row=latest_row,
                task_type=task_type,
                predicted_value=prediction_value,
                confidence_score=confidence_score,
            )
            technical = explanation["technical_state_summary"]
            news = explanation["news_sentiment_summary"]
            benchmark_summary = explanation["benchmark_strength_summary"]

            position = holdings_by_ticker.get(symbol)
            bullish, bearish = _derive_signal_flags(prediction_value, task_type, min_predicted_return_pct)
            external_context: dict[str, Any] | None = None
            if position or bullish:
                try:
                    external_context = build_external_market_context(
                        symbol,
                        company_name=snapshot.get("company_name"),
                    )
                except Exception as exc:  # pragma: no cover - provider guard
                    logger.warning("External context skipped ticker=%s error=%s", symbol, exc)
                    external_context = {
                        "missing_sources": ["external_context_error"],
                        "provider_notes": [str(exc)],
                    }
            context_score = _score_virtual_trader_context(
                latest_row=latest_row,
                snapshot=snapshot,
                external_context=external_context,
            )
            try:
                learned_context = (
                    get_model_feedback_service().get_context_adjustment(
                        factors=list(context_score["factors"]),
                        ticker=symbol,
                    )
                )
            except Exception as exc:  # pragma: no cover - feedback guard
                logger.warning(
                    "Context feedback skipped ticker=%s error=%s",
                    symbol,
                    exc,
                )
                learned_context = {
                    "adjustment": 0.0,
                    "matched_factors": [],
                }
            feedback_adjustment = float(
                learned_context.get("adjustment") or 0.0
            )
            if abs(feedback_adjustment) >= 0.05:
                context_score["score"] = max(
                    0.0,
                    min(
                        100.0,
                        float(context_score["score"])
                        + feedback_adjustment,
                    ),
                )
                context_score["factors"].append(
                    "historical outcome feedback adjusted context "
                    f"{feedback_adjustment:+.1f}"
                )
                context_score["label"] = (
                    "supportive"
                    if context_score["score"] >= MIN_CONTEXT_SCORE_FOR_BUY
                    else "cautious"
                    if context_score["score"] >= 40
                    else "weak"
                )
                context_score["summary"] = (
                    f"Context score {context_score['score']:.0f}/100: "
                    + "; ".join(context_score["factors"][:4])
                    + "."
                )
            context_score["feedback_adjustment"] = feedback_adjustment
            context_score["feedback_evidence"] = list(
                learned_context.get("matched_factors") or []
            )
            action = "no_action"
            reason = model_fallback_reason if decision_source == "fallback_rule" else "model_not_bullish"
            quantity = 0.0
            volatility = float(latest_row.get("rolling_volatility_20_pct", 0.0) or 0.0)

            if position and float(position["quantity"]) > 0:
                entry = float(position["avg_entry_price"])
                whole_holding_quantity = math.floor(float(position["quantity"]))
                quantity = float(whole_holding_quantity)
                if whole_holding_quantity < MIN_TRADE_QUANTITY:
                    action, reason = "hold", "holding_position"
                elif stop_loss_pct > 0 and current_price <= entry * (1 - stop_loss_pct):
                    action, reason = "sell", "stop_loss"
                elif portfolio_risk["reduce_positions"]:
                    action, reason = "sell", "portfolio_drawdown_reduction"
                elif take_profit_pct is not None and current_price >= entry * (1 + take_profit_pct):
                    action, reason = "sell", "take_profit"
                elif bearish and _confidence_ok(confidence_score, confidence_threshold):
                    action, reason = "sell", "model_bearish_signal"
                elif context_score["score"] <= MAX_CONTEXT_SCORE_FOR_HOLD and _confidence_ok(confidence_score, confidence_threshold):
                    action, reason = "sell", "context_risk_reduction"
                else:
                    action, reason = "hold", "holding_position"
                if action == "sell":
                    quantity = float(
                        _calculate_sell_quantity(
                            holding_quantity=whole_holding_quantity,
                            reason=reason,
                        )
                    )
            else:
                if bullish and _confidence_ok(confidence_score, confidence_threshold):
                    account = ledger.build_account_summary(clean_user_id, latest_prices=latest_price_cache)
                    cash_available = float(account["cash"])
                    equity = float(account["total_account_value"])
                    holdings_count = len(account["holdings"])
                    concentration_ok = holdings_count < 15
                    valuation_ok = pe_ratio is None or float(pe_ratio) <= 85
                    volatility_ok = volatility <= 55
                    effective_position_size_pct = (
                        max_position_size_pct
                        * float(portfolio_risk["position_size_multiplier"])
                        * float(market_regime["position_size_multiplier"])
                    )
                    allocation = min(
                        cash_available,
                        float(equity * effective_position_size_pct),
                    )
                    trade_admin_fee_usd = get_trade_admin_fee_usd()
                    affordable_quantity = math.floor(
                        (allocation - trade_admin_fee_usd) / current_price
                        if current_price > 0
                        else 0.0
                    )
                    context_ok = context_score["score"] >= MIN_CONTEXT_SCORE_FOR_BUY
                    if (
                        affordable_quantity >= MIN_TRADE_QUANTITY
                        and concentration_ok
                        and valuation_ok
                        and volatility_ok
                        and context_ok
                        and portfolio_risk["buy_allowed"]
                        and market_regime["new_position_allowed"]
                        and data_quality["trade_safe"]
                    ):
                        action, reason = "buy", "model_bullish_signal"
                        quantity = float(affordable_quantity)
                    else:
                        action = "no_action"
                        reason = (
                            "market_data_quality_block"
                            if not data_quality["trade_safe"]
                            else "portfolio_drawdown_pause"
                            if not portfolio_risk["buy_allowed"]
                            else "market_regime_stress"
                            if not market_regime["new_position_allowed"]
                            else "context_score_too_low"
                            if not context_ok
                            else "risk_or_cash_constraint"
                        )
                elif not _confidence_ok(confidence_score, confidence_threshold):
                    action, reason = "no_action", "confidence_below_threshold"

            threshold_summary = (
                f"Thresholds: confidence {confidence_score:.0%} vs {confidence_threshold:.0%}; "
                f"max position {max_position_size_pct:.0%}; stop loss {stop_loss_pct:.0%}; "
                f"whole-share interval 1; minimum quantity {MIN_TRADE_QUANTITY:g}; "
                f"normal sell size {PARTIAL_SELL_FRACTION:.0%}; "
                f"trade cost HKD {TRADE_ADMIN_FEE_HKD:.0f} (~USD {get_trade_admin_fee_usd():.2f}); "
                f"volatility20 {volatility:.1f}%; PE {pe_ratio if pe_ratio is not None else 'N/A'}; "
                f"context score {context_score['score']:.0f}/100."
                if confidence_score is not None
                else (
                    f"Thresholds: confidence unavailable; required {confidence_threshold:.0%}; "
                    f"max position {max_position_size_pct:.0%}; stop loss {stop_loss_pct:.0%}; "
                    f"whole-share interval 1; minimum quantity {MIN_TRADE_QUANTITY:g}; "
                    f"normal sell size {PARTIAL_SELL_FRACTION:.0%}; "
                    f"trade cost HKD {TRADE_ADMIN_FEE_HKD:.0f} (~USD {get_trade_admin_fee_usd():.2f}); "
                    f"volatility20 {volatility:.1f}%; PE {pe_ratio if pe_ratio is not None else 'N/A'}; "
                    f"context score {context_score['score']:.0f}/100."
                )
            )
            if portfolio_risk["performance_vs_contributions_pct"] is not None:
                threshold_summary += (
                    f" Portfolio risk {portfolio_risk['level']}; "
                    "equity versus contributions "
                    f"{portfolio_risk['performance_vs_contributions_pct']:+.1f}%; "
                    "position multiplier "
                    f"{portfolio_risk['position_size_multiplier']:.0%}."
                )
            threshold_summary += (
                f" Market data {data_quality['status']}"
                + (
                    f" ({', '.join(data_quality['reasons'][:3])})."
                    if data_quality["reasons"]
                    else "."
                )
            )
            threshold_summary += (
                f" Market regime {market_regime['level']}; "
                f"position multiplier {market_regime['position_size_multiplier']:.0%}"
                + (
                    f" ({', '.join(market_regime['reasons'][:3])})."
                    if market_regime["reasons"]
                    else "."
                )
            )

            latest_trade_for_symbol = store.list_trades(clean_user_id, limit=1, ticker=symbol)
            if action in {"buy", "sell"} and latest_trade_for_symbol:
                previous = latest_trade_for_symbol[0]
                now_dt = datetime.now(UTC)
                if _is_trade_cooldown_active(
                    latest_trade=previous,
                    action=action,
                    now_utc=now_dt,
                    cooldown_minutes=signal_cooldown_minutes,
                ):
                    action, reason = "no_action", "signal_cooldown_active"
                    quantity = 0.0
                elif (
                    previous.get("action") == action
                    and previous.get("reason") == reason
                ):
                    action, reason = "no_action", "duplicate_signal_suppressed"
                    quantity = 0.0

            now_ts = _utc_now()
            if action == "buy":
                try:
                    ledger.create_trade_event(
                        user_id=clean_user_id,
                        action="buy",
                        ticker=symbol,
                        quantity=quantity,
                        price=current_price,
                        source="trader",
                        reason=reason,
                        metadata={
                            "model_name": decision_model_name,
                            "model_period": decision_model_period,
                            "validation_score": decision_validation_score,
                            "confidence_score": confidence_score,
                            "prediction_value": prediction_value,
                            "decision_source": decision_source,
                        },
                    )
                except AccountLedgerError as exc:
                    action, reason = "no_action", f"ledger_rejected_buy:{exc}"
                    quantity = 0.0
            elif action == "sell" and position:
                try:
                    ledger.create_trade_event(
                        user_id=clean_user_id,
                        action="sell",
                        ticker=symbol,
                        quantity=quantity,
                        price=current_price,
                        source="trader",
                        reason=reason,
                        metadata={
                            "model_name": decision_model_name,
                            "model_period": decision_model_period,
                            "validation_score": decision_validation_score,
                            "confidence_score": confidence_score,
                            "prediction_value": prediction_value,
                            "decision_source": decision_source,
                        },
                    )
                except AccountLedgerError as exc:
                    action, reason = "no_action", f"ledger_rejected_sell:{exc}"
                    quantity = 0.0

            account = ledger.build_account_summary(
                clean_user_id,
                latest_prices=latest_price_cache,
            )
            updated_pos = {item["ticker"]: item for item in account["holdings"]}.get(symbol)
            holdings_after = float(updated_pos["quantity"]) if updated_pos else 0.0
            unrealized_after = float(updated_pos["unrealized_pnl"]) if updated_pos else 0.0
            action_summary = {
                "buy": "Simulated buy executed from model/fallback signal.",
                "sell": (
                    "Full position sold because the stop-loss was reached."
                    if reason == "stop_loss"
                    else "Part of the position sold in whole shares; remaining shares stay invested."
                ),
                "hold": "Holding position. No exit trigger was hit.",
                "no_action": "No action taken. Entry conditions were not met.",
            }.get(action, "No action.")

            trade_payload = {
                "timestamp": now_ts,
                "user_id": clean_user_id,
                "ticker": symbol,
                "action": action,
                "quantity": float(quantity),
                "price": float(current_price),
                "model_name": decision_model_name,
                "confidence_score": confidence_score,
                "reason": reason,
                "threshold_summary": threshold_summary,
                "technical_state_summary": technical,
                "news_sentiment_summary": news,
                "benchmark_strength_summary": benchmark_summary,
                "action_summary": action_summary,
                "cash_after": float(account["cash"]),
                "holdings_after": holdings_after,
                "realized_pnl": float(account["realized_pnl"]),
                "unrealized_pnl": unrealized_after,
                "metadata": {
                    "prediction_value": prediction_value,
                    "task_type": task_type,
                    "price_date": str(latest_row.get("date")),
                    "explanation": explanation["explanation"],
                    "pe_ratio": pe_ratio,
                    "market_cap": snapshot.get("market_cap"),
                    "sector": snapshot.get("sector"),
                    "industry": snapshot.get("industry"),
                    "volatility": volatility,
                    "decision_source": decision_source,
                    "model_validation_status": (
                        "validated"
                        if decision_source in {
                            "production_model",
                            "validated_candidate",
                            "shared_global_production",
                            "shared_global_candidate",
                        }
                        else "safety_fallback"
                        if decision_source == "fallback_rule"
                        else "user_requested_unvalidated"
                    ),
                    "model_period": decision_model_period,
                    "model_version": decision_model_version,
                    "validation_score": decision_validation_score,
                    "runtime_score": decision_runtime_score,
                    "prediction_uncertainty": decision_uncertainty,
                    "benchmark_shadow": benchmark_shadow,
                    "market_data_quality": data_quality,
                    "market_regime": market_regime,
                    "feedback_summary": decision_feedback_summary,
                    "context_score": context_score["score"],
                    "context_label": context_score["label"],
                    "context_summary": context_score["summary"],
                    "context_factors": context_score["factors"],
                    "context_feedback_adjustment": context_score[
                        "feedback_adjustment"
                    ],
                    "context_feedback_evidence": context_score[
                        "feedback_evidence"
                    ],
                    "external_context": external_context or {},
                    "headline_context": {
                        "political_risk_ratio_recent_7d": _safe_float(
                            latest_row.get("political_risk_article_ratio_recent_7d")
                        ),
                        "public_interest_ratio_recent_7d": _safe_float(
                            latest_row.get("public_interest_article_ratio_recent_7d")
                        ),
                        "analyst_positive_ratio_recent_7d": _safe_float(
                            latest_row.get("analyst_positive_ratio_recent_7d")
                        ),
                        "analyst_negative_ratio_recent_7d": _safe_float(
                            latest_row.get("analyst_negative_ratio_recent_7d")
                        ),
                        "earnings_positive_ratio_recent_7d": _safe_float(
                            latest_row.get("earnings_positive_ratio_recent_7d")
                        ),
                        "earnings_negative_ratio_recent_7d": _safe_float(
                            latest_row.get("earnings_negative_ratio_recent_7d")
                        ),
                    },
                    "missing_context": context_score["missing_context"],
                    "portfolio_risk": portfolio_risk,
                    "model_load_errors": model_load_errors,
                },
            }
            store.append_trade(trade_payload)
            try:
                feedback_service = get_model_feedback_service()
                feedback_service.record_decision(
                    trade_payload,
                    benchmark=benchmark,
                )
                feedback_service.record_benchmark_shadow(trade_payload)
            except Exception as exc:  # pragma: no cover - feedback guard
                logger.warning(
                    "Model feedback recording skipped ticker=%s error=%s",
                    symbol,
                    exc,
                )
            decisions.append(trade_payload)
            logger.info(
                "Live trader decision user_id=%s ticker=%s action=%s reason=%s prediction=%s confidence=%s source=%s",
                clean_user_id,
                symbol,
                action,
                reason,
                prediction_value,
                confidence_score,
                decision_source,
            )
        except Exception as exc:
            failed_symbols.append(symbol)
            logger.warning("Live trader ticker=%s skipped due to decision error: %s", symbol, exc)
            continue

    account = ledger.build_account_summary(
        clean_user_id,
        latest_prices=latest_price_cache,
    )
    live_curve = build_live_equity_curve(
        user_id=clean_user_id,
        latest_prices=latest_price_cache,
        limit=240,
    )
    holdings = account["holdings"]
    final_portfolio_risk = _build_portfolio_risk_state(account)
    holdings_value = float(account["holdings_value"])
    total_equity = float(account["total_account_value"])
    logger.info(
        "Live trader summary consistency profile_id=%s summary_cash=%.2f summary_holdings=%.2f summary_total=%.2f curve_latest=%.2f curve_ts=%s",
        clean_user_id,
        float(account["cash"]),
        holdings_value,
        total_equity,
        float(live_curve["latest_total_equity"]),
        live_curve["curve_last_point_timestamp"],
    )

    return LiveStatus(
        user_id=clean_user_id,
        model_name=AUTO_TRADING_MODEL_NAME if auto_model_selection else requested_model_name,
        generated_at_utc=_utc_now(),
        account={
            "snapshot_timestamp": account["as_of"],
            "curve_last_point_timestamp": live_curve["curve_last_point_timestamp"],
            "cash": float(account["cash"]),
            "realized_pnl": float(account["realized_pnl"]),
            "total_contributions_applied": float(account["net_deposits"]),
            "holdings_value": holdings_value,
            "total_equity": total_equity,
            "unrealized_pnl": float(account["unrealized_pnl"]),
            "net_deposits": float(account["net_deposits"]),
            "portfolio_risk_level": final_portfolio_risk["level"],
            "performance_vs_contributions_pct": final_portfolio_risk[
                "performance_vs_contributions_pct"
            ],
            "buying_paused": not final_portfolio_risk["buy_allowed"],
            "position_size_multiplier": final_portfolio_risk[
                "position_size_multiplier"
            ],
        },
        holdings=holdings,
        latest_decisions=decisions,
        contribution_events=contribution_events,
        universe_size=len(symbols),
        tickers_evaluated=len(decisions),
        tickers_failed=len(failed_symbols),
        fallback_used_count=fallback_used_count,
        equity_curve=live_curve["points"],
    )


def get_live_virtual_trader_status(
    user_id: str,
    tickers: list[str] | None = None,
    model_name: str | None = AUTO_TRADING_MODEL_NAME,
    auto_run: bool = False,
) -> LiveStatus:
    if auto_run:
        return run_live_virtual_trader_now(user_id=user_id, tickers=tickers, model_name=model_name)

    clean_user_id = str(user_id).strip()
    if not clean_user_id:
        raise LiveVirtualTraderError("user_id is required.")

    symbols = _resolve_user_tickers(clean_user_id, tickers)
    ledger = get_account_ledger_service()

    store = get_live_virtual_trader_store()
    # Read-only status should be quick. A full live run evaluates the whole
    # universe; a status refresh only needs current valuation for open holdings.
    account = ledger.build_account_summary(clean_user_id)
    latest_prices = dict(account.get("latest_prices") or {})
    live_curve = build_live_equity_curve(
        user_id=clean_user_id,
        latest_prices=latest_prices,
        limit=240,
    )
    holdings = account["holdings"]
    portfolio_risk = _build_portfolio_risk_state(account)
    holdings_value = float(account["holdings_value"])
    total_equity = float(account["total_account_value"])
    trade_filter = symbols[0] if tickers and len(symbols) == 1 else None
    latest_decisions = store.list_trades(
        clean_user_id,
        limit=20,
        ticker=trade_filter,
    )
    contribution_events = ledger.list_events(
        clean_user_id,
        limit=24,
        event_types=["monthly_contribution", "manual_deposit", "withdrawal"],
    )
    logger.info(
        "Live trader status consistency profile_id=%s summary_cash=%.2f summary_holdings=%.2f summary_total=%.2f curve_latest=%.2f curve_ts=%s",
        clean_user_id,
        float(account["cash"]),
        holdings_value,
        total_equity,
        float(live_curve["latest_total_equity"]),
        live_curve["curve_last_point_timestamp"],
    )

    return LiveStatus(
        user_id=clean_user_id,
        model_name=str(model_name or AUTO_TRADING_MODEL_NAME).strip().lower(),
        generated_at_utc=_utc_now(),
        account={
            "snapshot_timestamp": account["as_of"],
            "curve_last_point_timestamp": live_curve["curve_last_point_timestamp"],
            "cash": float(account["cash"]),
            "realized_pnl": float(account["realized_pnl"]),
            "total_contributions_applied": float(account["net_deposits"]),
            "holdings_value": holdings_value,
            "total_equity": total_equity,
            "unrealized_pnl": float(account["unrealized_pnl"]),
            "net_deposits": float(account["net_deposits"]),
            "portfolio_risk_level": portfolio_risk["level"],
            "performance_vs_contributions_pct": portfolio_risk[
                "performance_vs_contributions_pct"
            ],
            "buying_paused": not portfolio_risk["buy_allowed"],
            "position_size_multiplier": portfolio_risk[
                "position_size_multiplier"
            ],
        },
        holdings=holdings,
        latest_decisions=latest_decisions,
        contribution_events=contribution_events,
        universe_size=len(symbols),
        tickers_evaluated=len(symbols),
        tickers_failed=0,
        fallback_used_count=sum(
            1
            for item in latest_decisions
            if str((item.get("metadata") or {}).get("decision_source", "")) == "fallback_rule"
        ),
        equity_curve=live_curve["points"],
    )


def list_live_virtual_trader_trades(
    user_id: str,
    limit: int = 50,
    ticker: str | None = None,
) -> dict[str, Any]:
    clean_user_id = str(user_id).strip()
    if not clean_user_id:
        raise LiveVirtualTraderError("user_id is required.")
    store = get_live_virtual_trader_store()
    trades = store.list_trades(clean_user_id, limit=limit, ticker=ticker)
    return {
        "user_id": clean_user_id,
        "count": len(trades),
        "trades": trades,
        "contribution_application_history": get_account_ledger_service().list_events(
            clean_user_id,
            limit=100,
            event_types=["monthly_contribution", "manual_deposit", "withdrawal"],
        ),
    }


_STORE = LiveVirtualTraderStore()


def get_live_virtual_trader_store() -> LiveVirtualTraderStore:
    return _STORE
