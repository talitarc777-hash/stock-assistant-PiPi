"""Outcome feedback for live model decisions and contextual reasoning."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import math
from pathlib import Path
import sqlite3
from typing import Any, Callable

import pandas as pd

from app.core.settings import get_settings
from app.services.account_ledger_service import TRADE_ADMIN_FEE_HKD, get_trade_admin_fee_usd
from app.services.market_data import get_price_history
from app.services.market_config import normalize_market, resolve_model_identity, resolve_security

logger = logging.getLogger(__name__)
PriceLoader = Callable[[str, str], pd.DataFrame]
BENCHMARK_SHADOW_ROUND_TRIP_COST_PCT = 0.10
MODEL_FEEDBACK_ELIGIBLE_SOURCES = (
    "production_model",
    "validated_candidate",
    "shared_global_production",
    "shared_global_candidate",
    "saved_model",
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _decision_date(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata") or {}
    value = metadata.get("price_date") or payload.get("timestamp") or _utc_now_iso()
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return datetime.now(UTC).date().isoformat()
    return parsed.date().isoformat()


def _clean_history(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty or "date" not in frame.columns or "close" not in frame.columns:
        return pd.DataFrame()
    result = frame[["date", "close"]].copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.date
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    return result.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)


def _horizon_prices(
    history: pd.DataFrame,
    decision_date: str,
    horizon_days: int,
) -> tuple[str, float, float] | None:
    frame = _clean_history(history)
    if frame.empty:
        return None
    start_date = pd.to_datetime(decision_date, errors="coerce")
    if pd.isna(start_date):
        return None
    matches = frame.index[frame["date"] >= start_date.date()].tolist()
    if not matches:
        return None
    start_index = int(matches[0])
    end_index = start_index + max(1, int(horizon_days))
    if end_index >= len(frame):
        return None
    return (
        frame.iloc[end_index]["date"].isoformat(),
        float(frame.iloc[start_index]["close"]),
        float(frame.iloc[end_index]["close"]),
    )


def _prediction_direction(prediction: float, task_type: str) -> float:
    """Interpret a stored prediction using the same semantics as the trader."""
    clean_task = str(task_type or "").strip().lower()
    if clean_task == "classification":
        predicted_class = int(round(float(prediction)))
        if predicted_class == 1:
            return 1.0
        if predicted_class == 0:
            return -1.0
    return 1.0 if prediction > 0 else -1.0 if prediction < 0 else 0.0


def _load_market_history(
    price_loader: PriceLoader,
    *,
    ticker: str,
    market: str,
    period: str = "3mo",
) -> pd.DataFrame:
    """Load one market's candles without exposing provider symbols to storage."""
    identity = resolve_security(ticker, market)
    if price_loader is get_price_history:
        return get_price_history(identity.ticker, period, market=identity.market)
    # Existing tests and injected loaders use the original two-argument contract.
    symbol = identity.provider_symbol if identity.market == "HK" else identity.ticker
    return price_loader(symbol, period)


class ModelFeedbackService:
    """SQLite-backed prediction feedback and adaptive context statistics."""

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
                CREATE TABLE IF NOT EXISTS model_decision_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_key TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    model_ticker TEXT NOT NULL DEFAULT '',
                    benchmark TEXT NOT NULL,
                    decision_date TEXT NOT NULL,
                    recorded_at_utc TEXT NOT NULL,
                    horizon_days INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    model_period TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    decision_source TEXT NOT NULL,
                    task_type TEXT NOT NULL DEFAULT 'unknown',
                    action TEXT NOT NULL,
                    prediction_value REAL NOT NULL,
                    confidence_score REAL,
                    context_score REAL,
                    decision_price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    context_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    evaluated_at_utc TEXT,
                    outcome_date TEXT,
                    outcome_price REAL,
                    actual_return_pct REAL,
                    benchmark_return_pct REAL,
                    excess_return_pct REAL,
                    strategy_net_return_pct REAL,
                    estimated_cost_pct REAL,
                    direction_correct INTEGER,
                    profitable_after_cost INTEGER,
                    outcome_score REAL
                    ,market TEXT NOT NULL DEFAULT 'US'
                )
                """
            )
            feedback_columns = {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info(model_decision_feedback)"
                ).fetchall()
            }
            if "market" not in feedback_columns:
                conn.execute(
                    "ALTER TABLE model_decision_feedback "
                    "ADD COLUMN market TEXT NOT NULL DEFAULT 'US'"
                )
            if "model_ticker" not in feedback_columns:
                conn.execute(
                    "ALTER TABLE model_decision_feedback "
                    "ADD COLUMN model_ticker TEXT NOT NULL DEFAULT ''"
                )
            if "task_type" not in feedback_columns:
                conn.execute(
                    "ALTER TABLE model_decision_feedback "
                    "ADD COLUMN task_type TEXT NOT NULL DEFAULT 'unknown'"
                )
            conn.execute(
                """
                UPDATE model_decision_feedback
                SET model_ticker = 'GLOBAL'
                WHERE model_ticker = ''
                  AND decision_source IN (
                      'shared_global_production',
                      'shared_global_candidate'
                  )
                """
            )
            conn.execute(
                """
                UPDATE model_decision_feedback
                SET model_ticker = ticker
                WHERE model_ticker = ''
                  AND decision_source IN (
                      'production_model',
                      'validated_candidate',
                      'shared_global_production',
                      'shared_global_candidate'
                  )
                """
            )
            conn.execute(
                """
                UPDATE model_decision_feedback
                SET task_type = 'classification'
                WHERE task_type = 'unknown'
                  AND model_name = 'logistic_regression'
                """
            )
            conn.execute(
                """
                UPDATE model_decision_feedback
                SET task_type = 'regression'
                WHERE task_type = 'unknown'
                  AND model_name = 'linear_regression'
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_model_feedback_lookup
                ON model_decision_feedback(
                    market, ticker, model_period, model_name, status, decision_date
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_model_feedback_market_lookup
                ON model_decision_feedback(
                    market, ticker, model_period, model_name, status, decision_date
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_model_feedback_evaluation
                ON model_decision_feedback(
                    status, decision_source, decision_date, id
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_model_feedback_origin
                ON model_decision_feedback(
                    model_ticker, model_period, model_name, status, decision_date
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS benchmark_shadow_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observation_key TEXT NOT NULL UNIQUE,
                    ticker TEXT NOT NULL,
                    benchmark TEXT NOT NULL,
                    decision_date TEXT NOT NULL,
                    recorded_at_utc TEXT NOT NULL,
                    horizon_days INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    model_period TEXT NOT NULL,
                    prediction INTEGER NOT NULL,
                    outperform_probability REAL,
                    decision_price REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    evaluated_at_utc TEXT,
                    outcome_date TEXT,
                    stock_return_pct REAL,
                    benchmark_return_pct REAL,
                    excess_return_pct REAL,
                    active_net_return_pct REAL,
                    direction_correct INTEGER,
                    profitable_after_cost INTEGER,
                    market TEXT NOT NULL DEFAULT 'US'
                )
                """
            )
            shadow_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(benchmark_shadow_feedback)").fetchall()
            }
            if "market" not in shadow_columns:
                conn.execute(
                    "ALTER TABLE benchmark_shadow_feedback "
                    "ADD COLUMN market TEXT NOT NULL DEFAULT 'US'"
                )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_benchmark_shadow_lookup
                ON benchmark_shadow_feedback(
                    ticker, model_period, model_name, status, decision_date
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_benchmark_shadow_market_lookup
                ON benchmark_shadow_feedback(
                    market, ticker, model_period, model_name, status, decision_date
                )
                """
            )
            conn.commit()

    def record_benchmark_shadow(
        self,
        payload: dict[str, Any],
    ) -> bool:
        """Record one non-executing benchmark-relative observation per date."""
        if not get_settings().model_feedback_enabled:
            return False
        metadata = dict(payload.get("metadata") or {})
        market = normalize_market(payload.get("market") or metadata.get("market") or "US")
        shadow = dict(metadata.get("benchmark_shadow") or {})
        if shadow.get("status") != "available" or shadow.get("execution_enabled"):
            return False
        ticker = resolve_security(str(payload.get("ticker") or ""), market).ticker
        benchmark = resolve_security(
            str(shadow.get("benchmark") or ("2800" if market == "HK" else "VOO")),
            market,
        ).ticker
        model_name = str(shadow.get("model_name") or "").strip().lower()
        model_period = str(shadow.get("model_period") or "").strip()
        prediction = int(shadow.get("prediction"))
        price = _safe_float(payload.get("price"))
        if (
            not ticker
            or ticker == benchmark
            or not model_name
            or not model_period
            or prediction not in {0, 1}
            or price is None
            or price <= 0
        ):
            return False
        decision_date = _decision_date(payload)
        observation_key = "|".join(
            (market, ticker, benchmark, model_period, model_name, decision_date)
        )
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO benchmark_shadow_feedback (
                    observation_key, ticker, benchmark, decision_date,
                    recorded_at_utc, horizon_days, model_name, model_period,
                    prediction, outperform_probability, decision_price, market
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_key,
                    ticker,
                    benchmark,
                    decision_date,
                    _utc_now_iso(),
                    int(get_settings().model_feedback_horizon_days),
                    model_name,
                    model_period,
                    prediction,
                    _safe_float(shadow.get("outperform_probability")),
                    price,
                    market,
                ),
            )
            conn.commit()
        return bool(cursor.rowcount)

    def list_benchmark_shadow_feedback(
        self,
        *,
        ticker: str | None = None,
        model_period: str | None = None,
        model_name: str | None = None,
        status: str | None = None,
        market: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if market:
            clauses.append("market = ?")
            params.append(normalize_market(market))
        if ticker:
            clauses.append("ticker = ?")
            params.append(str(ticker).strip().upper())
        if model_period:
            clauses.append("model_period = ?")
            params.append(str(model_period).strip())
        if model_name:
            clauses.append("model_name = ?")
            params.append(str(model_name).strip().lower())
        if status:
            clauses.append("status = ?")
            params.append(str(status).strip().lower())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM benchmark_shadow_feedback
                {where}
                ORDER BY decision_date DESC, id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_decision(
        self,
        payload: dict[str, Any],
        *,
        benchmark: str = "VOO",
    ) -> bool:
        """Persist one daily observation for a model/version/ticker."""
        if not get_settings().model_feedback_enabled:
            return False
        metadata = dict(payload.get("metadata") or {})
        market = normalize_market(payload.get("market") or metadata.get("market") or "US")
        identity = resolve_security(str(payload.get("ticker") or ""), market)
        ticker = identity.ticker
        model_name = str(payload.get("model_name") or "").strip().lower()
        if not ticker or not model_name:
            return False

        date_value = _decision_date(payload)
        model_period = str(metadata.get("model_period") or "unknown")
        model_version = str(metadata.get("model_version") or "legacy")
        source = str(metadata.get("decision_source") or "unknown")
        if source not in MODEL_FEEDBACK_ELIGIBLE_SOURCES:
            return False
        model_ticker = str(metadata.get("model_ticker") or ticker).strip().upper()
        model_ticker = resolve_model_identity(model_ticker, market).ticker
        task_type = str(metadata.get("task_type") or "unknown").strip().lower()
        decision_key = "|".join(
            (
                market,
                ticker,
                model_ticker,
                model_period,
                model_name,
                model_version,
                date_value,
            )
        )
        context = {
            "model_ticker": model_ticker,
            "task_type": task_type,
            "context_score": metadata.get("context_score"),
            "context_label": metadata.get("context_label"),
            "context_factors": list(metadata.get("context_factors") or []),
            "context_summary": metadata.get("context_summary"),
            "headline_context": metadata.get("headline_context") or {},
            "external_context": metadata.get("external_context") or {},
            "pe_ratio": metadata.get("pe_ratio"),
            "market_cap": metadata.get("market_cap"),
            "sector": metadata.get("sector"),
            "industry": metadata.get("industry"),
            "volatility": metadata.get("volatility"),
        }
        prediction = _safe_float(metadata.get("prediction_value"))
        price = _safe_float(payload.get("price"))
        if prediction is None or price is None or price <= 0:
            return False

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO model_decision_feedback (
                    decision_key, user_id, ticker, model_ticker, benchmark, decision_date,
                    recorded_at_utc, horizon_days, model_name, model_period,
                    model_version, decision_source, task_type, action, prediction_value,
                    confidence_score, context_score, decision_price, quantity,
                    context_json, market
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_key,
                    str(payload.get("user_id") or "system"),
                    ticker,
                    model_ticker,
                    str(benchmark or "VOO").strip().upper(),
                    date_value,
                    _utc_now_iso(),
                    int(get_settings().model_feedback_horizon_days),
                    model_name,
                    model_period,
                    model_version,
                    source,
                    task_type,
                    str(payload.get("action") or "no_action").lower(),
                    prediction,
                    _safe_float(payload.get("confidence_score")),
                    _safe_float(metadata.get("context_score")),
                    price,
                    float(_safe_float(payload.get("quantity")) or 0.0),
                    json.dumps(context, ensure_ascii=False),
                    market,
                ),
            )
            conn.commit()
        return bool(cursor.rowcount)

    def list_feedback(
        self,
        *,
        ticker: str | None = None,
        model_period: str | None = None,
        model_name: str | None = None,
        status: str | None = None,
        market: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        clean_market = normalize_market(market) if market else None
        if clean_market:
            clauses.append("market = ?")
            params.append(clean_market)
        if ticker:
            clauses.append("ticker = ?")
            params.append(resolve_security(ticker, clean_market or "US").ticker)
        if model_period:
            clauses.append("model_period = ?")
            params.append(str(model_period).strip())
        if model_name:
            clauses.append("model_name = ?")
            params.append(str(model_name).strip().lower())
        if status:
            clauses.append("status = ?")
            params.append(str(status).strip().lower())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM model_decision_feedback
                {where}
                ORDER BY decision_date DESC, id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["context"] = json.loads(item.pop("context_json") or "{}")
            except json.JSONDecodeError:
                item["context"] = {}
                item.pop("context_json", None)
            output.append(item)
        return output

    def _pending_feedback_for_evaluation(
        self,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return oldest eligible rows so mature work cannot be starved."""
        placeholders = ", ".join("?" for _ in MODEL_FEEDBACK_ELIGIBLE_SOURCES)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM model_decision_feedback
                WHERE status = 'pending'
                  AND decision_source IN ({placeholders})
                ORDER BY decision_date ASC, id ASC
                LIMIT ?
                """,
                (*MODEL_FEEDBACK_ELIGIBLE_SOURCES, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def _pending_feedback_count(self) -> int:
        placeholders = ", ".join("?" for _ in MODEL_FEEDBACK_ELIGIBLE_SOURCES)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM model_decision_feedback
                WHERE status = 'pending'
                  AND decision_source IN ({placeholders})
                """,
                MODEL_FEEDBACK_ELIGIBLE_SOURCES,
            ).fetchone()
        return int(row["count"] or 0)

    def get_pipeline_status(self, market: str = "US") -> dict[str, Any]:
        """Return observable queue coverage without overstating model quality."""
        clean_market = normalize_market(market)
        placeholders = ", ".join("?" for _ in MODEL_FEEDBACK_ELIGIBLE_SOURCES)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total_count,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE WHEN status = 'evaluated' THEN 1 ELSE 0 END) AS evaluated_count,
                    COUNT(DISTINCT ticker) AS ticker_count,
                    MIN(CASE WHEN status = 'pending' THEN decision_date END) AS oldest_pending_date,
                    MAX(CASE WHEN status = 'evaluated' THEN outcome_date END) AS latest_outcome_date
                FROM model_decision_feedback
                WHERE market = ?
                  AND decision_source IN ({placeholders})
                """,
                (clean_market, *MODEL_FEEDBACK_ELIGIBLE_SOURCES),
            ).fetchone()
        counts = dict(row) if row else {}
        oldest = counts.get("oldest_pending_date")
        estimated_maturity = None
        if oldest:
            estimated_maturity = str(
                (
                    pd.Timestamp(oldest)
                    + pd.offsets.BDay(int(get_settings().model_feedback_horizon_days))
                ).date()
            )
        return {
            "market": clean_market,
            "total_observation_count": int(counts.get("total_count") or 0),
            "pending_count": int(counts.get("pending_count") or 0),
            "evaluated_count": int(counts.get("evaluated_count") or 0),
            "ticker_count": int(counts.get("ticker_count") or 0),
            "oldest_pending_date": oldest,
            "estimated_oldest_maturity_date": estimated_maturity,
            "latest_outcome_date": counts.get("latest_outcome_date"),
            "horizon_trading_days": int(get_settings().model_feedback_horizon_days),
            "minimum_samples_for_score_influence": int(
                get_settings().model_feedback_min_samples
            ),
        }

    def _pending_benchmark_shadows_for_evaluation(
        self,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM benchmark_shadow_feedback
                WHERE status = 'pending'
                ORDER BY decision_date ASC, id ASC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _outcome_score(
        *,
        direction_correct: bool,
        strategy_net_return_pct: float,
        excess_return_pct: float,
    ) -> float:
        profitability = strategy_net_return_pct > 0
        net_component = _clamp(0.5 + strategy_net_return_pct / 10.0, 0.0, 1.0)
        excess_component = _clamp(0.5 + excess_return_pct / 10.0, 0.0, 1.0)
        return _clamp(
            (0.45 if direction_correct else 0.0)
            + (0.25 if profitability else 0.0)
            + 0.20 * net_component
            + 0.10 * excess_component,
            0.0,
            1.0,
        )

    def evaluate_pending(
        self,
        *,
        price_loader: PriceLoader = get_price_history,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Evaluate pending decisions once the future trading-day row exists."""
        if not get_settings().model_feedback_enabled:
            return {"evaluated": 0, "pending": 0, "errors": []}
        pending = self._pending_feedback_for_evaluation(limit=limit)
        history_cache: dict[str, pd.DataFrame] = {}
        evaluated = 0
        errors: list[str] = []

        for row in pending:
            ticker = str(row["ticker"])
            benchmark = str(row["benchmark"])
            market = normalize_market(row.get("market") or "US")
            try:
                ticker_key = f"{market}:{ticker}"
                benchmark_key = f"{market}:{benchmark}"
                if ticker_key not in history_cache:
                    history_cache[ticker_key] = _load_market_history(
                        price_loader, ticker=ticker, market=market
                    )
                if benchmark_key not in history_cache:
                    history_cache[benchmark_key] = _load_market_history(
                        price_loader, ticker=benchmark, market=market
                    )
                ticker_prices = _horizon_prices(
                    history_cache[ticker_key],
                    str(row["decision_date"]),
                    int(row["horizon_days"]),
                )
                benchmark_prices = _horizon_prices(
                    history_cache[benchmark_key],
                    str(row["decision_date"]),
                    int(row["horizon_days"]),
                )
                if ticker_prices is None or benchmark_prices is None:
                    continue

                outcome_date, market_start, outcome_price = ticker_prices
                _, benchmark_start, benchmark_end = benchmark_prices
                decision_price = float(row["decision_price"] or market_start)
                actual_return = ((outcome_price / decision_price) - 1.0) * 100.0
                benchmark_return = ((benchmark_end / benchmark_start) - 1.0) * 100.0
                prediction = float(row["prediction_value"])
                direction = _prediction_direction(
                    prediction,
                    str(row.get("task_type") or "unknown"),
                )
                direction_correct = (
                    (direction > 0 and actual_return > 0)
                    or (direction < 0 and actual_return < 0)
                    or (direction == 0 and abs(actual_return) < 0.25)
                )
                notional = abs(float(row["quantity"]) * decision_price)
                round_trip_fee = (
                    2.0 * TRADE_ADMIN_FEE_HKD
                    if market == "HK"
                    else 2.0 * get_trade_admin_fee_usd()
                )
                cost_pct = (
                    (round_trip_fee / notional) * 100.0
                    if row["action"] in {"buy", "sell"} and notional > 0
                    else 0.0
                )
                strategy_net = direction * actual_return - cost_pct
                excess = direction * (actual_return - benchmark_return)
                outcome_score = self._outcome_score(
                    direction_correct=direction_correct,
                    strategy_net_return_pct=strategy_net,
                    excess_return_pct=excess,
                )
                with self._connect() as conn:
                    conn.execute(
                        """
                        UPDATE model_decision_feedback
                        SET status = 'evaluated',
                            evaluated_at_utc = ?,
                            outcome_date = ?,
                            outcome_price = ?,
                            actual_return_pct = ?,
                            benchmark_return_pct = ?,
                            excess_return_pct = ?,
                            strategy_net_return_pct = ?,
                            estimated_cost_pct = ?,
                            direction_correct = ?,
                            profitable_after_cost = ?,
                            outcome_score = ?
                        WHERE id = ? AND status = 'pending'
                        """,
                        (
                            _utc_now_iso(),
                            outcome_date,
                            outcome_price,
                            actual_return,
                            benchmark_return,
                            excess,
                            strategy_net,
                            cost_pct,
                            1 if direction_correct else 0,
                            1 if strategy_net > 0 else 0,
                            outcome_score,
                            int(row["id"]),
                        ),
                    )
                    conn.commit()
                evaluated += 1
            except Exception as exc:  # pragma: no cover - provider guard
                errors.append(f"{ticker}:{exc}")
                logger.warning(
                    "Model feedback evaluation skipped market=%s ticker=%s error=%s",
                    market,
                    ticker,
                    exc,
                )

        remaining = self._pending_feedback_count()
        shadow_result = self.evaluate_pending_benchmark_shadows(
            price_loader=price_loader,
            limit=limit,
        )
        return {
            "evaluated": evaluated,
            "pending": remaining,
            "errors": errors[:20],
            "shadow_evaluated": shadow_result["evaluated"],
            "shadow_pending": shadow_result["pending"],
            "shadow_errors": shadow_result["errors"],
        }

    def evaluate_pending_benchmark_shadows(
        self,
        *,
        price_loader: PriceLoader = get_price_history,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Settle shadow observations only after the future row exists."""
        pending = self._pending_benchmark_shadows_for_evaluation(limit=limit)
        history_cache: dict[str, pd.DataFrame] = {}
        evaluated = 0
        errors: list[str] = []
        for row in pending:
            ticker = str(row["ticker"])
            benchmark = str(row["benchmark"])
            market = normalize_market(row.get("market") or "US")
            try:
                ticker_key = f"{market}:{ticker}"
                benchmark_key = f"{market}:{benchmark}"
                if ticker_key not in history_cache:
                    history_cache[ticker_key] = _load_market_history(
                        price_loader, ticker=ticker, market=market
                    )
                if benchmark_key not in history_cache:
                    history_cache[benchmark_key] = _load_market_history(
                        price_loader, ticker=benchmark, market=market
                    )
                ticker_prices = _horizon_prices(
                    history_cache[ticker_key],
                    str(row["decision_date"]),
                    int(row["horizon_days"]),
                )
                benchmark_prices = _horizon_prices(
                    history_cache[benchmark_key],
                    str(row["decision_date"]),
                    int(row["horizon_days"]),
                )
                if ticker_prices is None or benchmark_prices is None:
                    continue
                outcome_date, stock_start, stock_end = ticker_prices
                _, benchmark_start, benchmark_end = benchmark_prices
                stock_return = (stock_end / stock_start - 1.0) * 100.0
                benchmark_return = (
                    benchmark_end / benchmark_start - 1.0
                ) * 100.0
                excess_return = stock_return - benchmark_return
                prediction = int(row["prediction"])
                actual_outperform = (
                    excess_return > BENCHMARK_SHADOW_ROUND_TRIP_COST_PCT
                )
                direction_correct = prediction == int(actual_outperform)
                active_net_return = (
                    stock_return - BENCHMARK_SHADOW_ROUND_TRIP_COST_PCT
                    if prediction == 1
                    else None
                )
                profitable = (
                    int(active_net_return > 0)
                    if active_net_return is not None
                    else None
                )
                with self._connect() as conn:
                    conn.execute(
                        """
                        UPDATE benchmark_shadow_feedback
                        SET status = 'evaluated', evaluated_at_utc = ?,
                            outcome_date = ?, stock_return_pct = ?,
                            benchmark_return_pct = ?, excess_return_pct = ?,
                            active_net_return_pct = ?, direction_correct = ?,
                            profitable_after_cost = ?
                        WHERE id = ? AND status = 'pending'
                        """,
                        (
                            _utc_now_iso(),
                            outcome_date,
                            stock_return,
                            benchmark_return,
                            excess_return,
                            active_net_return,
                            1 if direction_correct else 0,
                            profitable,
                            int(row["id"]),
                        ),
                    )
                    conn.commit()
                evaluated += 1
            except Exception as exc:  # pragma: no cover - provider guard
                errors.append(f"{ticker}:{exc}")
                logger.warning(
                    "Benchmark shadow evaluation skipped market=%s ticker=%s error=%s",
                    market,
                    ticker,
                    exc,
                )
        remaining = len(
            self.list_benchmark_shadow_feedback(status="pending", limit=10000)
        )
        return {
            "evaluated": evaluated,
            "pending": remaining,
            "errors": errors[:20],
        }

    def get_benchmark_shadow_summary(
        self,
        *,
        ticker: str,
        model_period: str,
        model_name: str,
    ) -> dict[str, Any]:
        """Return genuinely forward benchmark-relative evidence."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT prediction, direction_correct, excess_return_pct,
                       active_net_return_pct, profitable_after_cost
                FROM benchmark_shadow_feedback
                WHERE ticker = ? AND model_period = ? AND model_name = ?
                  AND status = 'evaluated'
                ORDER BY decision_date DESC
                LIMIT 120
                """,
                (
                    str(ticker).strip().upper(),
                    str(model_period),
                    str(model_name).strip().lower(),
                ),
            ).fetchall()
            observation = conn.execute(
                """
                SELECT COUNT(*) AS total_count,
                       SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count
                FROM benchmark_shadow_feedback
                WHERE ticker = ? AND model_period = ? AND model_name = ?
                """,
                (str(ticker).strip().upper(), str(model_period), str(model_name).strip().lower()),
            ).fetchone()
            latest = conn.execute(
                """
                SELECT decision_date, status, outcome_date
                FROM benchmark_shadow_feedback
                WHERE ticker = ? AND model_period = ? AND model_name = ?
                ORDER BY decision_date DESC, id DESC LIMIT 1
                """,
                (str(ticker).strip().upper(), str(model_period), str(model_name).strip().lower()),
            ).fetchone()
            next_pending = conn.execute(
                """
                SELECT decision_date, horizon_days
                FROM benchmark_shadow_feedback
                WHERE ticker = ? AND model_period = ? AND model_name = ?
                  AND status = 'pending'
                ORDER BY decision_date ASC, id ASC LIMIT 1
                """,
                (str(ticker).strip().upper(), str(model_period), str(model_name).strip().lower()),
            ).fetchone()
        active = [row for row in rows if int(row["prediction"]) == 1]

        def average(items: list[sqlite3.Row], column: str) -> float | None:
            values = [float(row[column]) for row in items if row[column] is not None]
            return sum(values) / len(values) if values else None

        estimated_maturity_date = None
        if next_pending:
            try:
                estimated_maturity_date = str(
                    (
                        pd.Timestamp(next_pending["decision_date"])
                        + pd.offsets.BDay(int(next_pending["horizon_days"] or 5))
                    ).date()
                )
            except (TypeError, ValueError):
                estimated_maturity_date = None

        return {
            "sample_count": len(rows),
            "pending_count": int(observation["pending_count"] or 0),
            "total_observation_count": int(observation["total_count"] or 0),
            "latest_observation_date": latest["decision_date"] if latest else None,
            "latest_observation_status": latest["status"] if latest else None,
            "latest_outcome_date": latest["outcome_date"] if latest else None,
            "next_pending_observation_date": next_pending["decision_date"] if next_pending else None,
            "estimated_next_maturity_date": estimated_maturity_date,
            "maturity_horizon_trading_days": int(next_pending["horizon_days"] or 5) if next_pending else None,
            "active_signal_count": len(active),
            "direction_accuracy": average(rows, "direction_correct"),
            "average_excess_return_pct": average(rows, "excess_return_pct"),
            "average_active_net_return_pct": average(
                active,
                "active_net_return_pct",
            ),
            "active_profitable_rate": average(active, "profitable_after_cost"),
            "minimum_samples_for_promotion": int(
                get_settings().model_feedback_min_samples
            ),
        }

    def get_model_summary(
        self,
        *,
        ticker: str,
        model_period: str,
        model_name: str,
        model_version: str | None = None,
        market: str = "US",
    ) -> dict[str, Any]:
        clean_market = normalize_market(market)
        identity = resolve_model_identity(ticker, clean_market)
        source_placeholders = ", ".join(
            "?" for _ in MODEL_FEEDBACK_ELIGIBLE_SOURCES
        )
        clauses = [
            "market = ?",
            "COALESCE(NULLIF(model_ticker, ''), ticker) = ?",
            "model_period = ?",
            "model_name = ?",
            "status = 'evaluated'",
            f"decision_source IN ({source_placeholders})",
        ]
        params: list[Any] = [
            clean_market,
            identity.ticker,
            str(model_period),
            str(model_name).strip().lower(),
            *MODEL_FEEDBACK_ELIGIBLE_SOURCES,
        ]
        if model_version:
            clauses.append("model_version = ?")
            params.append(str(model_version))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT direction_correct, profitable_after_cost,
                       actual_return_pct, strategy_net_return_pct,
                       excess_return_pct, outcome_score
                FROM model_decision_feedback
                WHERE {' AND '.join(clauses)}
                ORDER BY decision_date DESC
                LIMIT 120
                """,
                tuple(params),
            ).fetchall()

        sample_count = len(rows)
        if not rows:
            return {
                "sample_count": 0,
                "direction_accuracy": None,
                "profitable_rate": None,
                "average_actual_return_pct": None,
                "average_strategy_net_return_pct": None,
                "average_excess_return_pct": None,
                "raw_feedback_score": 0.5,
                "feedback_score": 0.5,
                "reliability": 0.0,
            }

        def average(column: str) -> float:
            values = [
                float(row[column])
                for row in rows
                if row[column] is not None
            ]
            return sum(values) / len(values) if values else 0.0

        raw_score = average("outcome_score")
        minimum = get_settings().model_feedback_min_samples
        reliability = min(1.0, sample_count / float(minimum))
        feedback_score = 0.5 + (raw_score - 0.5) * reliability
        return {
            "sample_count": sample_count,
            "direction_accuracy": average("direction_correct"),
            "profitable_rate": average("profitable_after_cost"),
            "average_actual_return_pct": average("actual_return_pct"),
            "average_strategy_net_return_pct": average(
                "strategy_net_return_pct"
            ),
            "average_excess_return_pct": average("excess_return_pct"),
            "raw_feedback_score": raw_score,
            "feedback_score": _clamp(feedback_score, 0.0, 1.0),
            "reliability": reliability,
        }

    def blend_validation_with_feedback(
        self,
        *,
        validation_score: float,
        feedback_summary: dict[str, Any],
    ) -> float:
        samples = int(feedback_summary.get("sample_count") or 0)
        minimum = get_settings().model_feedback_min_samples
        if samples < minimum:
            return _clamp(validation_score, 0.0, 1.0)
        weight = get_settings().model_feedback_promotion_weight
        feedback_score = float(
            feedback_summary.get("feedback_score") or 0.5
        )
        return _clamp(
            (1.0 - weight) * float(validation_score)
            + weight * feedback_score,
            0.0,
            1.0,
        )

    def get_context_adjustment(
        self,
        *,
        factors: list[str],
        ticker: str | None = None,
        market: str = "US",
    ) -> dict[str, Any]:
        """Learn a small buy-context adjustment from past factor outcomes."""
        clean_factors = [
            str(item).strip()
            for item in factors
            if str(item).strip()
        ]
        if not clean_factors:
            return {"adjustment": 0.0, "matched_factors": []}
        source_placeholders = ", ".join(
            "?" for _ in MODEL_FEEDBACK_ELIGIBLE_SOURCES
        )
        clauses = [
            "market = ?",
            "status = 'evaluated'",
            "actual_return_pct IS NOT NULL",
            f"decision_source IN ({source_placeholders})",
        ]
        clean_market = normalize_market(market)
        params: list[Any] = [clean_market, *MODEL_FEEDBACK_ELIGIBLE_SOURCES]
        if ticker:
            clauses.append("ticker = ?")
            params.append(resolve_security(ticker, clean_market).ticker)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT context_json, actual_return_pct
                FROM model_decision_feedback
                WHERE {' AND '.join(clauses)}
                ORDER BY decision_date DESC
                LIMIT 600
                """,
                tuple(params),
            ).fetchall()

        returns_by_factor: dict[str, list[float]] = {
            factor: [] for factor in clean_factors
        }
        for row in rows:
            try:
                context = json.loads(row["context_json"] or "{}")
            except json.JSONDecodeError:
                continue
            historical = set(
                str(item)
                for item in context.get("context_factors") or []
            )
            for factor in clean_factors:
                if factor in historical:
                    returns_by_factor[factor].append(
                        float(row["actual_return_pct"])
                    )

        matched: list[dict[str, Any]] = []
        components: list[float] = []
        for factor, values in returns_by_factor.items():
            if len(values) < 3:
                continue
            average_return = sum(values) / len(values)
            reliability = min(1.0, len(values) / 10.0)
            component = (
                _clamp(average_return / 2.0, -2.0, 2.0)
                * reliability
            )
            components.append(component)
            matched.append(
                {
                    "factor": factor,
                    "samples": len(values),
                    "average_5d_return_pct": average_return,
                    "adjustment": component,
                }
            )
        maximum = get_settings().context_feedback_max_adjustment
        adjustment = _clamp(sum(components), -maximum, maximum)
        return {
            "adjustment": adjustment,
            "matched_factors": matched,
        }


_SERVICE: ModelFeedbackService | None = None


def get_model_feedback_service() -> ModelFeedbackService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = ModelFeedbackService()
    return _SERVICE
