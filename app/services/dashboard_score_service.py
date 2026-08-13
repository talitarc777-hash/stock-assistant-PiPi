"""Persistent, market-aware technical-score rankings for the Dashboard.

The score in the Dashboard is the existing deterministic indicator score.  It
is deliberately independent from model validation: model readiness is attached
as diagnostics, but never suppresses an otherwise valid technical score.
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from functools import lru_cache
import json
import logging
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Callable

from app.core.settings import get_settings
from app.services.hkex_security_metadata import get_hk_security_metadata
from app.services.indicators import add_technical_indicators
from app.services.market_config import normalize_market, resolve_security
from app.services.market_data import get_price_history
from app.services.model_lifecycle_service import (
    TRADING_TARGET_NAME,
    get_model_lifecycle_service,
)
from app.services.scoring import score_from_indicators
from app.services.ticker_classification import (
    TickerClassification,
    classify_ticker,
)
from app.services.watchlist_service import get_user_watchlist

logger = logging.getLogger(__name__)

DEFAULT_SCORE_PERIOD = "5y"
DEFAULT_CACHE_MAX_AGE = timedelta(minutes=30)
TRADING_MODEL_PERIODS = ("2y", "5y", "10y")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_utc(value: datetime) -> str:
    clean = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return clean.astimezone(UTC).replace(microsecond=0).isoformat()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_load(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _hk_primary_class(category: str | None, subcategory: str | None) -> str:
    """Normalize official HKEX category text without guessing from a name."""
    description = f"{category or ''} {subcategory or ''}".strip().lower()
    if "real estate investment trust" in description or "reit" in description:
        return "reit"
    if "exchange traded" in description or " etf" in f" {description}":
        return "etf"
    if "debt" in description or "bond" in description:
        return "fixed_income"
    if any(
        token in description
        for token in ("warrant", "derivative", "structured product", "callable bull", "callable bear")
    ):
        return "derivative"
    if "equity" in description:
        return "stock"
    return "unknown"


def _default_classification_provider(ticker: str, market: str) -> TickerClassification:
    if market != "HK":
        return classify_ticker(ticker)

    metadata = get_hk_security_metadata(ticker)
    if metadata is None:
        return classify_ticker(ticker)
    primary_class = _hk_primary_class(metadata.category, metadata.subcategory)
    return classify_ticker(
        ticker,
        market_metadata={"primary_ticker_class": primary_class},
    )


def _default_score_provider(ticker: str, market: str, period: str) -> dict[str, Any]:
    price_df = get_price_history(ticker=ticker, period=period, market=market)
    indicator_df = add_technical_indicators(price_df)
    score = score_from_indicators(indicator_df)
    return {
        "latest_close": float(indicator_df.iloc[-1]["close"]),
        "score_breakdown": {
            "trend_score": score.trend_score,
            "momentum_score": score.momentum_score,
            "confirmation_score": score.confirmation_score,
            "risk_penalty": score.risk_penalty,
            "total_score": score.total_score,
        },
        "label": score.label,
    }


def _default_model_status_provider(ticker: str, market: str) -> dict[str, Any]:
    try:
        candidates = get_model_lifecycle_service().resolve_runtime_model_candidates(
            ticker=ticker,
            market=market,
            period=DEFAULT_SCORE_PERIOD,
            periods=TRADING_MODEL_PERIODS,
            target_name=TRADING_TARGET_NAME,
        )
    except Exception as exc:  # Model status must never prevent technical scoring.
        logger.warning(
            "Dashboard model-status lookup failed market=%s ticker=%s error=%s",
            market,
            ticker,
            exc,
        )
        return {
            "model_name": "rule_based_fallback",
            "model_ticker": ticker,
            "model_period": None,
            "model_source": "fallback_rule",
            "model_status": "lookup_failed",
        }
    if not candidates:
        return {
            "model_name": "rule_based_fallback",
            "model_ticker": ticker,
            "model_period": None,
            "model_source": "fallback_rule",
            "model_status": "no_validated_model",
        }
    selected = candidates[0]
    return {
        "model_name": selected.get("model_name"),
        "model_ticker": selected.get("ticker"),
        "model_period": selected.get("period"),
        "model_source": selected.get("source"),
        "model_status": selected.get("status"),
    }


class DashboardScoreService:
    """Score each active ticker and retain its latest successful result."""

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        watchlist_provider: Callable[[str, str], Any] | None = None,
        score_provider: Callable[[str, str, str], dict[str, Any]] | None = None,
        classification_provider: Callable[[str, str], TickerClassification] | None = None,
        model_status_provider: Callable[[str, str], dict[str, Any]] | None = None,
        now_provider: Callable[[], datetime] | None = None,
        cache_max_age: timedelta = DEFAULT_CACHE_MAX_AGE,
    ) -> None:
        self.db_path = Path(db_path or get_settings().profile_db_path)
        self.watchlist_provider = watchlist_provider or get_user_watchlist
        self.score_provider = score_provider or _default_score_provider
        self.classification_provider = classification_provider or _default_classification_provider
        self.model_status_provider = model_status_provider or _default_model_status_provider
        self.now_provider = now_provider or _utc_now
        self.cache_max_age = cache_max_age
        self._refresh_lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dashboard_ticker_scores (
                    user_id TEXT NOT NULL,
                    market TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    period TEXT NOT NULL,
                    latest_close REAL NOT NULL,
                    trend_score INTEGER NOT NULL,
                    momentum_score INTEGER NOT NULL,
                    confirmation_score INTEGER NOT NULL,
                    risk_penalty INTEGER NOT NULL,
                    total_score INTEGER NOT NULL,
                    label TEXT NOT NULL,
                    primary_ticker_class TEXT NOT NULL,
                    stock_subclass TEXT,
                    classification_source TEXT NOT NULL,
                    model_name TEXT,
                    model_ticker TEXT,
                    model_period TEXT,
                    model_source TEXT,
                    model_status TEXT,
                    scored_at_utc TEXT NOT NULL,
                    PRIMARY KEY (user_id, market, ticker, period)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dashboard_score_refresh_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    market TEXT NOT NULL,
                    period TEXT NOT NULL,
                    active_tickers_json TEXT NOT NULL,
                    expected_tickers_json TEXT NOT NULL,
                    scored_tickers_json TEXT NOT NULL,
                    failures_json TEXT NOT NULL,
                    started_at_utc TEXT NOT NULL,
                    completed_at_utc TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def active_universe(self, user_id: str, market: str) -> dict[str, Any]:
        clean_market = normalize_market(market)
        result = self.watchlist_provider(user_id, clean_market)
        if isinstance(result, tuple):
            tickers = result[0]
            using_system_default = bool(result[1]) if len(result) > 1 else False
        else:
            tickers = result
            using_system_default = False
        normalized: list[str] = []
        for ticker in tickers or []:
            symbol = resolve_security(str(ticker), clean_market).ticker
            if symbol not in normalized:
                normalized.append(symbol)
        return {
            "user_id": user_id,
            "market": clean_market,
            "active_count": len(normalized),
            "active_tickers": normalized,
            "using_system_default": using_system_default,
        }

    def refresh(self, *, user_id: str, market: str, period: str = DEFAULT_SCORE_PERIOD) -> dict[str, Any]:
        clean_market = normalize_market(market)
        with self._refresh_lock:
            universe = self.active_universe(user_id, clean_market)
            expected = list(universe["active_tickers"])
            started_at = _iso_utc(self.now_provider())
            scored: list[str] = []
            failures: list[dict[str, str]] = []

            with closing(self._connect()) as connection:
                for ticker in expected:
                    try:
                        classification = self.classification_provider(ticker, clean_market)
                        score_payload = self.score_provider(ticker, clean_market, period)
                        breakdown = dict(score_payload["score_breakdown"])
                        total_score = int(breakdown["total_score"])
                        if not 0 <= total_score <= 100:
                            raise ValueError("technical score is outside 0..100")
                        model = self.model_status_provider(ticker, clean_market)
                        scored_at = _iso_utc(self.now_provider())
                        connection.execute(
                            """
                            INSERT INTO dashboard_ticker_scores (
                                user_id, market, ticker, period, latest_close,
                                trend_score, momentum_score, confirmation_score,
                                risk_penalty, total_score, label,
                                primary_ticker_class, stock_subclass,
                                classification_source, model_name, model_ticker,
                                model_period, model_source, model_status, scored_at_utc
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(user_id, market, ticker, period) DO UPDATE SET
                                latest_close=excluded.latest_close,
                                trend_score=excluded.trend_score,
                                momentum_score=excluded.momentum_score,
                                confirmation_score=excluded.confirmation_score,
                                risk_penalty=excluded.risk_penalty,
                                total_score=excluded.total_score,
                                label=excluded.label,
                                primary_ticker_class=excluded.primary_ticker_class,
                                stock_subclass=excluded.stock_subclass,
                                classification_source=excluded.classification_source,
                                model_name=excluded.model_name,
                                model_ticker=excluded.model_ticker,
                                model_period=excluded.model_period,
                                model_source=excluded.model_source,
                                model_status=excluded.model_status,
                                scored_at_utc=excluded.scored_at_utc
                            """,
                            (
                                user_id,
                                clean_market,
                                ticker,
                                period,
                                float(score_payload["latest_close"]),
                                int(breakdown.get("trend_score", 0)),
                                int(breakdown.get("momentum_score", 0)),
                                int(breakdown.get("confirmation_score", 0)),
                                int(breakdown.get("risk_penalty", 0)),
                                total_score,
                                str(score_payload.get("label") or ""),
                                classification.primary_ticker_class,
                                classification.stock_subclass,
                                classification.classification_source,
                                model.get("model_name"),
                                model.get("model_ticker"),
                                model.get("model_period"),
                                model.get("model_source"),
                                model.get("model_status"),
                                scored_at,
                            ),
                        )
                        scored.append(ticker)
                    except Exception as exc:
                        logger.warning(
                            "Dashboard score refresh skipped market=%s ticker=%s error=%s",
                            clean_market,
                            ticker,
                            exc,
                        )
                        failures.append({"ticker": ticker, "reason": f"{type(exc).__name__}: {exc}"})

                completed_at = _iso_utc(self.now_provider())
                connection.execute(
                    """
                    INSERT INTO dashboard_score_refresh_runs (
                        user_id, market, period, active_tickers_json,
                        expected_tickers_json, scored_tickers_json, failures_json,
                        started_at_utc, completed_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        clean_market,
                        period,
                        _json_dump(expected),
                        _json_dump(expected),
                        _json_dump(scored),
                        _json_dump(failures),
                        started_at,
                        completed_at,
                    ),
                )
                connection.commit()

            logger.info(
                "Dashboard scores refreshed user_id=%s market=%s expected=%d scored=%d failed=%d",
                user_id,
                clean_market,
                len(expected),
                len(scored),
                len(failures),
            )
            return self.diagnostics(user_id=user_id, market=clean_market, period=period)

    @staticmethod
    def _row_to_result(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "ticker": str(row["ticker"]),
            "market": str(row["market"]),
            "period": str(row["period"]),
            "score_source": "technical_indicators",
            "model_applied_to_score": False,
            "latest_close": float(row["latest_close"]),
            "score_breakdown": {
                "trend_score": int(row["trend_score"]),
                "momentum_score": int(row["momentum_score"]),
                "confirmation_score": int(row["confirmation_score"]),
                "risk_penalty": int(row["risk_penalty"]),
                "total_score": int(row["total_score"]),
            },
            "label": str(row["label"]),
            "primary_ticker_class": str(row["primary_ticker_class"]),
            "stock_subclass": str(row["stock_subclass"]) if row["stock_subclass"] else None,
            "classification_source": str(row["classification_source"]),
            "scored_at_utc": str(row["scored_at_utc"]),
            "model_name": str(row["model_name"]) if row["model_name"] else None,
            "model_ticker": str(row["model_ticker"]) if row["model_ticker"] else None,
            "model_period": str(row["model_period"]) if row["model_period"] else None,
            "model_source": str(row["model_source"]) if row["model_source"] else None,
            "model_status": str(row["model_status"]) if row["model_status"] else None,
        }

    def raw_scores(self, *, user_id: str, market: str, period: str = DEFAULT_SCORE_PERIOD) -> list[dict[str, Any]]:
        universe = self.active_universe(user_id, market)
        active = list(universe["active_tickers"])
        if not active:
            return []
        placeholders = ",".join("?" for _ in active)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM dashboard_ticker_scores
                WHERE user_id = ? AND market = ? AND period = ?
                  AND ticker IN ({placeholders})
                ORDER BY total_score DESC, ticker ASC
                """,
                (user_id, normalize_market(market), period, *active),
            ).fetchall()
        return [self._row_to_result(row) for row in rows]

    def _needs_refresh(self, *, user_id: str, market: str, period: str) -> bool:
        universe = self.active_universe(user_id, market)
        expected = set(universe["active_tickers"])
        rows = self.raw_scores(user_id=user_id, market=market, period=period)
        available = {row["ticker"] for row in rows}
        if expected - available:
            return True
        if not rows:
            return bool(expected)
        timestamps: list[datetime] = []
        for row in rows:
            try:
                value = datetime.fromisoformat(str(row["scored_at_utc"]))
                timestamps.append(value if value.tzinfo else value.replace(tzinfo=UTC))
            except ValueError:
                return True
        return self.now_provider().astimezone(UTC) - min(timestamps) >= self.cache_max_age

    def top_scores(
        self,
        *,
        user_id: str,
        market: str,
        period: str = DEFAULT_SCORE_PERIOD,
        asset_type: str = "all",
        limit: int = 10,
        refresh_if_stale: bool = True,
    ) -> dict[str, Any]:
        clean_asset_type = str(asset_type or "all").strip().lower()
        if clean_asset_type not in {"all", "stock", "etf"}:
            raise ValueError("asset_type must be all, stock, or etf.")
        refreshed = False
        if refresh_if_stale and self._needs_refresh(user_id=user_id, market=market, period=period):
            # Recheck after taking the process lock so simultaneous page loads
            # do not repeat the same market-data work.
            with self._refresh_lock:
                if self._needs_refresh(user_id=user_id, market=market, period=period):
                    self.refresh(user_id=user_id, market=market, period=period)
                    refreshed = True
        rows = self.raw_scores(user_id=user_id, market=market, period=period)
        if clean_asset_type != "all":
            rows = [row for row in rows if row["primary_ticker_class"] == clean_asset_type]
        rows = rows[: max(1, min(200, int(limit)))]
        return {
            "user_id": user_id,
            "market": normalize_market(market),
            "period": period,
            "asset_type": clean_asset_type,
            "count": len(rows),
            "refreshed": refreshed,
            "rows": rows,
            "diagnostics": self.diagnostics(user_id=user_id, market=market, period=period),
        }

    def diagnostics(self, *, user_id: str, market: str, period: str = DEFAULT_SCORE_PERIOD) -> dict[str, Any]:
        universe = self.active_universe(user_id, market)
        clean_market = universe["market"]
        rows = self.raw_scores(user_id=user_id, market=clean_market, period=period)
        with closing(self._connect()) as connection:
            run = connection.execute(
                """
                SELECT * FROM dashboard_score_refresh_runs
                WHERE user_id = ? AND market = ? AND period = ?
                ORDER BY id DESC LIMIT 1
                """,
                (user_id, clean_market, period),
            ).fetchone()
        expected = list(universe["active_tickers"])
        active_set = set(expected)
        cached_tickers = [row["ticker"] for row in rows]
        last_scored = (
            [
                ticker
                for ticker in _json_load(run["scored_tickers_json"], [])
                if ticker in active_set
            ]
            if run
            else cached_tickers
        )
        failures = (
            [
                item
                for item in _json_load(run["failures_json"], [])
                if item.get("ticker") in active_set
            ]
            if run
            else []
        )
        cache_timestamp = max((row["scored_at_utc"] for row in rows), default=None)
        return {
            **universe,
            "period": period,
            "expected_count": len(expected),
            "expected_tickers": expected,
            "scored_count": len(last_scored),
            "scored_tickers": last_scored,
            "skipped_count": len(failures),
            "skipped": failures,
            "cached_count": len(cached_tickers),
            "cached_tickers": cached_tickers,
            "missing_from_cache": [ticker for ticker in expected if ticker not in cached_tickers],
            "ranking_rows": len(rows),
            "cache_timestamp_utc": cache_timestamp,
            "last_refresh_started_at_utc": str(run["started_at_utc"]) if run else None,
            "last_refresh_completed_at_utc": str(run["completed_at_utc"]) if run else None,
            "score_records": rows,
        }


@lru_cache(maxsize=1)
def get_dashboard_score_service() -> DashboardScoreService:
    return DashboardScoreService()
