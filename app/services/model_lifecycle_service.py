"""Automatic model lifecycle management service.

This service keeps model governance beginner-friendly and explicit:
- tracks candidate / production / archived model status
- runs scheduled or trigger-based retraining workflows
- promotes only validated candidates
- provides runtime fallback hierarchy metadata for traders
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd

from app.core.settings import get_settings
from app.models.model_lifecycle import MODEL_REGISTRY_STATUSES, MODEL_WORKFLOW_TYPES
from app.services.model_results import (
    ModelResultsError,
    load_model_accuracy_summary,
    load_virtual_trader_summary,
)
from app.services.model_feedback_service import ModelFeedbackService
from app.services.model_training import (
    TrainingRunResult,
    VALIDATION_SCHEME_VERSION as TRAINING_VALIDATION_SCHEME_VERSION,
    train_baseline_models_for_ticker,
    train_pooled_baseline_models,
)
from app.services.research_pipeline import build_feature_dataset
from app.services.universe_service import get_active_universe
from app.services.market_config import (
    MARKET_CONFIGS,
    normalize_market,
    resolve_model_identity,
    resolve_security,
)

logger = logging.getLogger(__name__)

DEFAULT_TARGET_NAME = "target_5d_updown"
TRADING_TARGET_NAME = "target_5d_return"
OUTPERFORMANCE_TARGET_NAME = "target_5d_outperform"
DEFAULT_PERIOD = "5y"
TRADING_MODEL_PERIODS = ("2y", "5y", "10y")
DEFAULT_STALE_DAYS = 30
PRODUCTION_MIN_SCORE = 0.50
PROMOTION_DELTA = 0.005
MIN_WALK_FORWARD_ROWS = 30
MIN_DIRECTION_ACCURACY = 0.52
MIN_WORST_FOLD_ACCURACY = 0.45
MIN_DIRECTION_EDGE = 0.01
MIN_SIGNAL_MINORITY_RATE = 0.05
MIN_BALANCED_DIRECTION_ACCURACY = 0.55
MIN_WORST_CLASS_RECALL = 0.20
PROMOTION_EXECUTION_COST_PCT = 0.05
MIN_ACTIVE_SIGNAL_PROFITABLE_RATE = 0.48
MAX_PROMOTION_PROXY_DRAWDOWN_PCT = -25.0
VALIDATION_GATE_VERSION = 9
# Scheme 4 artifacts already used purged, time-ordered folds. Re-evaluate them
# through gate 9 at startup instead of discarding a sound incumbent solely
# because new challengers use the improved scheme 5 calibration.
MIN_VALIDATION_SCHEME_VERSION = 4
TRADING_TARGET_HORIZON_ROWS = 5
MIN_PROFITABLE_NON_OVERLAP_PATH_RATE = 0.60
MIN_POOLED_TICKER_PASS_RATE = 0.60
MIN_FORWARD_DIRECTION_ACCURACY = 0.55
MIN_FORWARD_ACTIVE_SIGNALS = 5
MIN_FORWARD_PROFITABLE_RATE = 0.50
MIN_ACTIVE_SIGNALS_PER_PATH = 5


class ModelLifecycleError(Exception):
    """Raised when model lifecycle operations fail validation or persistence."""


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _as_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _safe_json_load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _state_key_for_market(name: str, market: str) -> str:
    clean_market = normalize_market(market)
    return name if clean_market == "US" else f"{name}:{clean_market}"


class ModelLifecycleService:
    """SQLite-backed registry + retraining workflow service."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = Path(db_path or get_settings().profile_db_path)
        self.feedback_service = ModelFeedbackService(
            db_path=str(self.db_path)
        )
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
                CREATE TABLE IF NOT EXISTS model_registry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    period TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    is_validated INTEGER NOT NULL DEFAULT 0,
                    validation_score REAL,
                    stale_after_days INTEGER NOT NULL DEFAULT 30,
                    retrain_type TEXT,
                    last_trained_at_utc TEXT,
                    last_evaluated_at_utc TEXT,
                    last_promoted_at_utc TEXT,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(ticker, period, target_name, model_name)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_model_registry_lookup
                ON model_registry(ticker, period, target_name, status, is_validated, last_evaluated_at_utc)
                """
            )
            # Keep the original US registry untouched for rollback compatibility.
            # New code uses this market-keyed registry and copies legacy rows once.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_model_registry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market TEXT NOT NULL DEFAULT 'US',
                    ticker TEXT NOT NULL,
                    period TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    is_validated INTEGER NOT NULL DEFAULT 0,
                    validation_score REAL,
                    stale_after_days INTEGER NOT NULL DEFAULT 30,
                    retrain_type TEXT,
                    last_trained_at_utc TEXT,
                    last_evaluated_at_utc TEXT,
                    last_promoted_at_utc TEXT,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(market, ticker, period, target_name, model_name)
                )
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO market_model_registry (
                    market, ticker, period, target_name, model_name, status,
                    is_validated, validation_score, stale_after_days, retrain_type,
                    last_trained_at_utc, last_evaluated_at_utc, last_promoted_at_utc,
                    metrics_json, notes, created_at, updated_at
                )
                SELECT 'US', ticker, period, target_name, model_name, status,
                       is_validated, validation_score, stale_after_days, retrain_type,
                       last_trained_at_utc, last_evaluated_at_utc, last_promoted_at_utc,
                       metrics_json, notes, created_at, updated_at
                FROM model_registry
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_market_model_registry_lookup
                ON market_model_registry(
                    market, ticker, period, target_name, status,
                    is_validated, last_evaluated_at_utc
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS model_lifecycle_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_type TEXT NOT NULL,
                    trigger_reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at_utc TEXT NOT NULL,
                    completed_at_utc TEXT,
                    processed_tickers INTEGER NOT NULL DEFAULT 0,
                    successful_models INTEGER NOT NULL DEFAULT 0,
                    failed_models INTEGER NOT NULL DEFAULT 0,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS model_lifecycle_state (
                    state_key TEXT PRIMARY KEY,
                    state_value TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def get_state(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state_value FROM model_lifecycle_state WHERE state_key = ?",
                (str(key).strip(),),
            ).fetchone()
        return None if row is None else str(row["state_value"])

    def set_state(self, key: str, value: str) -> None:
        now = _utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO model_lifecycle_state(state_key, state_value, updated_at_utc)
                VALUES(?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    state_value = excluded.state_value,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (str(key).strip(), str(value), now),
            )
            conn.commit()

    @staticmethod
    def _is_stale(last_trained_at_utc: str | None, stale_after_days: int) -> bool:
        trained_at = _as_utc(last_trained_at_utc)
        if trained_at is None:
            return True
        age_days = (datetime.now(UTC) - trained_at).total_seconds() / 86400.0
        return age_days > max(1, int(stale_after_days))

    @staticmethod
    def _extract_metrics_value(metrics_summary: dict) -> dict:
        return dict(metrics_summary.get("metrics", {})) if isinstance(metrics_summary, dict) else {}

    def _validation_score(self, metrics_summary: dict, task_type: str, target_name: str) -> float:
        """Convert saved metrics into one compact promotion score [0, 1]."""
        metrics = self._extract_metrics_value(metrics_summary)
        task = str(task_type or metrics_summary.get("task_type", "")).lower()

        if target_name == "target_5d_updown" or task == "classification":
            for key in ("accuracy", "f1", "precision", "recall"):
                value = metrics.get(key)
                if isinstance(value, (int, float)):
                    return float(max(0.0, min(1.0, float(value))))
            return 0.0

        direction = metrics.get("direction_accuracy")
        if isinstance(direction, (int, float)):
            return float(max(0.0, min(1.0, float(direction))))

        rmse = metrics.get("rmse")
        if isinstance(rmse, (int, float)):
            return float(max(0.0, min(1.0, 1.0 / (1.0 + abs(float(rmse))))))

        return 0.0

    @staticmethod
    def _walk_forward_quality_gate(evaluation_table: pd.DataFrame | None) -> dict[str, Any]:
        """Reject fragile models before lifecycle promotion.

        Direction accuracy alone is misleading in a strongly rising or falling
        sample: a model can look accurate by nearly always choosing the majority
        direction. This gate requires genuine out-of-sample edge, fold stability,
        and both bullish and bearish signals.
        """
        required = {"predicted_value", "actual_future_result"}
        if evaluation_table is None or not required.issubset(evaluation_table.columns):
            return {
                "passed": False,
                "reasons": ["walk_forward_evaluation_missing"],
                "sample_count": 0,
            }

        frame = evaluation_table.copy()
        frame["_source_position"] = range(len(frame))
        frame["predicted_value"] = pd.to_numeric(frame["predicted_value"], errors="coerce")
        frame["actual_future_result"] = pd.to_numeric(
            frame["actual_future_result"],
            errors="coerce",
        )
        frame = frame.dropna(subset=["predicted_value", "actual_future_result"])
        actionable_filter_applied = "is_actionable_signal" in frame.columns
        if actionable_filter_applied:
            actionable = frame["is_actionable_signal"].astype(str).str.lower().isin(
                {"true", "1", "yes"}
            )
            frame = frame[actionable].copy()
        regime_filter_applied = "is_regime_trade_allowed" in frame.columns
        if regime_filter_applied:
            regime_allowed = frame["is_regime_trade_allowed"].astype(str).str.lower().isin(
                {"true", "1", "yes"}
            )
            frame = frame[regime_allowed].copy()
        sample_count = int(len(frame))
        effective_sample_count = int(frame["_source_position"].floordiv(
            TRADING_TARGET_HORIZON_ROWS
        ).nunique()) if sample_count else 0
        if sample_count == 0:
            return {
                "passed": False,
                "reasons": ["walk_forward_evaluation_empty"],
                "sample_count": 0,
            }

        direction_paths: list[dict[str, float | int]] = []
        for offset in range(TRADING_TARGET_HORIZON_ROWS):
            path = frame[
                frame["_source_position"] % TRADING_TARGET_HORIZON_ROWS == offset
            ]
            if path.empty:
                continue
            predicted_up = path["predicted_value"] > 0
            actual_up = path["actual_future_result"] > 0
            accuracy = float((predicted_up == actual_up).mean())
            actual_up_rate = float(actual_up.mean())
            naive_accuracy = max(actual_up_rate, 1.0 - actual_up_rate)
            predicted_up_rate = float(predicted_up.mean())
            true_positive = int((predicted_up & actual_up).sum())
            true_negative = int((~predicted_up & ~actual_up).sum())
            actual_positive = int(actual_up.sum())
            actual_negative = int((~actual_up).sum())
            positive_recall = (
                true_positive / actual_positive if actual_positive else 0.0
            )
            negative_recall = (
                true_negative / actual_negative if actual_negative else 0.0
            )
            balanced_accuracy = (positive_recall + negative_recall) / 2.0
            worst_class_recall = min(positive_recall, negative_recall)
            fold_accuracies: list[float] = []
            if "evaluation_window" in path.columns:
                for _, fold in path.groupby("evaluation_window", dropna=True):
                    fold_predicted_up = fold["predicted_value"] > 0
                    fold_actual_up = fold["actual_future_result"] > 0
                    fold_accuracies.append(float((fold_predicted_up == fold_actual_up).mean()))
            direction_paths.append(
                {
                    "offset": offset,
                    "sample_count": int(len(path)),
                    "direction_accuracy": accuracy,
                    "naive_majority_accuracy": naive_accuracy,
                    "direction_edge": accuracy - naive_accuracy,
                    "worst_fold_accuracy": min(fold_accuracies) if fold_accuracies else accuracy,
                    "predicted_up_rate": predicted_up_rate,
                    "signal_minority_rate": min(predicted_up_rate, 1.0 - predicted_up_rate),
                    "balanced_direction_accuracy": balanced_accuracy,
                    "positive_recall": positive_recall,
                    "negative_recall": negative_recall,
                    "worst_class_recall": worst_class_recall,
                }
            )

        direction_accuracy = float(pd.Series(
            [item["direction_accuracy"] for item in direction_paths]
        ).median())
        naive_accuracy = float(pd.Series(
            [item["naive_majority_accuracy"] for item in direction_paths]
        ).median())
        direction_edge = float(pd.Series(
            [item["direction_edge"] for item in direction_paths]
        ).median())
        worst_fold_accuracy = float(pd.Series(
            [item["worst_fold_accuracy"] for item in direction_paths]
        ).median())
        predicted_up_rate = float(pd.Series(
            [item["predicted_up_rate"] for item in direction_paths]
        ).median())
        signal_minority_rate = float(pd.Series(
            [item["signal_minority_rate"] for item in direction_paths]
        ).median())
        balanced_accuracy = float(pd.Series(
            [item["balanced_direction_accuracy"] for item in direction_paths]
        ).median())
        worst_class_recall = float(pd.Series(
            [item["worst_class_recall"] for item in direction_paths]
        ).median())
        positive_edge_path_rate = float(sum(
            float(item["direction_edge"]) >= MIN_DIRECTION_EDGE
            for item in direction_paths
        ) / len(direction_paths))

        reasons: list[str] = []
        if effective_sample_count < MIN_WALK_FORWARD_ROWS:
            reasons.append("insufficient_out_of_sample_rows")
        if direction_accuracy < MIN_DIRECTION_ACCURACY:
            reasons.append("direction_accuracy_below_minimum")
        if direction_edge < MIN_DIRECTION_EDGE:
            reasons.append("no_edge_over_majority_baseline")
        if worst_fold_accuracy < MIN_WORST_FOLD_ACCURACY:
            reasons.append("unstable_walk_forward_folds")
        if signal_minority_rate < MIN_SIGNAL_MINORITY_RATE:
            reasons.append("one_sided_predictions")
        if balanced_accuracy < MIN_BALANCED_DIRECTION_ACCURACY:
            reasons.append("balanced_accuracy_below_minimum")
        if worst_class_recall < MIN_WORST_CLASS_RECALL:
            reasons.append("minority_event_recall_below_minimum")
        if positive_edge_path_rate < MIN_PROFITABLE_NON_OVERLAP_PATH_RATE:
            reasons.append("direction_edge_not_robust_across_non_overlapping_paths")

        result = {
            "passed": not reasons,
            "reasons": reasons,
            "sample_count": sample_count,
            "effective_non_overlapping_sample_count": effective_sample_count,
            "direction_accuracy": direction_accuracy,
            "naive_majority_accuracy": naive_accuracy,
            "direction_edge": direction_edge,
            "worst_fold_accuracy": worst_fold_accuracy,
            "predicted_up_rate": predicted_up_rate,
            "signal_minority_rate": signal_minority_rate,
            "balanced_direction_accuracy": balanced_accuracy,
            "worst_class_recall": worst_class_recall,
            "positive_edge_non_overlapping_path_rate": positive_edge_path_rate,
            "non_overlapping_direction_paths": direction_paths,
            "actionable_filter_applied": actionable_filter_applied,
            "regime_filter_applied": regime_filter_applied,
        }
        if "source_ticker" in evaluation_table.columns:
            source_symbols = evaluation_table["source_ticker"].dropna().astype(str)
            if source_symbols.nunique() >= 3:
                per_ticker: dict[str, dict[str, Any]] = {}
                for symbol, ticker_frame in evaluation_table.groupby("source_ticker"):
                    per_ticker[str(symbol)] = ModelLifecycleService._walk_forward_quality_gate(
                        ticker_frame.drop(columns=["source_ticker"])
                    )
                pass_rate = sum(bool(item.get("passed")) for item in per_ticker.values()) / len(per_ticker)
                result["pooled_ticker_quality"] = per_ticker
                result["pooled_ticker_pass_rate"] = pass_rate
                if pass_rate < MIN_POOLED_TICKER_PASS_RATE:
                    result["reasons"].append("direction_edge_not_robust_across_tickers")
                    result["passed"] = False
        return result

    @staticmethod
    def _historical_trading_quality_gate(
        evaluation_table: pd.DataFrame | None,
        target_name: str,
    ) -> dict[str, Any]:
        """Evaluate whether out-of-sample signals had usable trading economics."""
        if target_name != TRADING_TARGET_NAME:
            return {
                "passed": True,
                "not_applicable": True,
                "reasons": [],
            }
        required = {"predicted_value", "actual_future_result"}
        if evaluation_table is None or not required.issubset(evaluation_table.columns):
            return {
                "passed": False,
                "reasons": ["trading_evaluation_missing"],
                "active_signal_count": 0,
            }

        frame = evaluation_table.copy()
        frame["_source_position"] = range(len(frame))
        frame["predicted_value"] = pd.to_numeric(frame["predicted_value"], errors="coerce")
        frame["actual_future_result"] = pd.to_numeric(
            frame["actual_future_result"],
            errors="coerce",
        )
        frame = frame.dropna(subset=["predicted_value", "actual_future_result"])
        actionable_filter_applied = "is_actionable_signal" in frame.columns
        if actionable_filter_applied:
            actionable = frame["is_actionable_signal"].astype(str).str.lower().isin(
                {"true", "1", "yes"}
            )
            frame = frame[actionable].copy()
        regime_filter_applied = "is_regime_trade_allowed" in frame.columns
        if regime_filter_applied:
            regime_allowed = frame["is_regime_trade_allowed"].astype(str).str.lower().isin(
                {"true", "1", "yes"}
            )
            frame = frame[regime_allowed].copy()
        if frame.empty:
            return {
                "passed": False,
                "reasons": ["trading_evaluation_empty"],
                "active_signal_count": 0,
            }

        path_metrics: list[dict[str, float | int]] = []
        for offset in range(TRADING_TARGET_HORIZON_ROWS):
            path = frame[
                frame["_source_position"] % TRADING_TARGET_HORIZON_ROWS == offset
            ].copy()
            if path.empty:
                continue
            active = path["predicted_value"] > 0
            if "market_regime_position_multiplier" in path.columns:
                regime_multiplier = pd.to_numeric(
                    path["market_regime_position_multiplier"],
                    errors="coerce",
                ).fillna(1.0).clip(lower=0.0, upper=1.0)
            else:
                regime_multiplier = pd.Series(1.0, index=path.index)
            signal_changes = active.astype(int).diff().abs().fillna(active.astype(int))
            strategy_returns_pct = (
                path["actual_future_result"].where(active, 0.0) * regime_multiplier
                - signal_changes * PROMOTION_EXECUTION_COST_PCT
            )
            active_returns = strategy_returns_pct[active]
            active_count = int(active.sum())
            profitable_rate = float((active_returns > 0).mean()) if active_count else 0.0
            average_return = float(active_returns.mean()) if active_count else 0.0

            wealth = 1.0
            peak = 1.0
            max_drawdown = 0.0
            for value in strategy_returns_pct:
                period_return = max(-1.0, float(value) / 100.0)
                wealth *= 1.0 + period_return
                peak = max(peak, wealth)
                if peak > 0:
                    max_drawdown = min(max_drawdown, wealth / peak - 1.0)
            path_metrics.append(
                {
                    "offset": offset,
                    "active_signal_count": active_count,
                    "profitable_signal_rate": profitable_rate,
                    "average_active_return_pct_after_cost": average_return,
                    "cumulative_signal_return_pct_after_cost": (wealth - 1.0) * 100.0,
                    "max_signal_drawdown_pct": max_drawdown * 100.0,
                }
            )

        if not path_metrics:
            return {
                "passed": False,
                "reasons": ["non_overlapping_trading_paths_missing"],
                "active_signal_count": 0,
            }

        active_signal_count = min(int(item["active_signal_count"]) for item in path_metrics)
        profitable_rate = float(pd.Series(
            [item["profitable_signal_rate"] for item in path_metrics]
        ).median())
        average_active_return_pct = float(pd.Series(
            [item["average_active_return_pct_after_cost"] for item in path_metrics]
        ).median())
        cumulative_return_pct = float(pd.Series(
            [item["cumulative_signal_return_pct_after_cost"] for item in path_metrics]
        ).median())
        max_drawdown_pct = min(float(item["max_signal_drawdown_pct"]) for item in path_metrics)
        profitable_path_rate = float(sum(
            float(item["average_active_return_pct_after_cost"]) > 0
            for item in path_metrics
        ) / len(path_metrics))

        reasons: list[str] = []
        if active_signal_count < MIN_ACTIVE_SIGNALS_PER_PATH:
            reasons.append("insufficient_active_signals")
        if average_active_return_pct <= 0:
            reasons.append("negative_average_net_signal_return")
        if profitable_rate < MIN_ACTIVE_SIGNAL_PROFITABLE_RATE:
            reasons.append("profitable_signal_rate_below_minimum")
        if max_drawdown_pct < MAX_PROMOTION_PROXY_DRAWDOWN_PCT:
            reasons.append("historical_signal_drawdown_too_large")
        if profitable_path_rate < MIN_PROFITABLE_NON_OVERLAP_PATH_RATE:
            reasons.append("returns_not_robust_across_non_overlapping_paths")

        result = {
            "passed": not reasons,
            "reasons": reasons,
            "active_signal_count": active_signal_count,
            "required_active_signals_per_non_overlapping_path": MIN_ACTIVE_SIGNALS_PER_PATH,
            "profitable_signal_rate": profitable_rate,
            "average_active_return_pct_after_cost": average_active_return_pct,
            "cumulative_signal_return_pct_after_cost": cumulative_return_pct,
            "max_signal_drawdown_pct": max_drawdown_pct,
            "execution_cost_pct_per_signal_change": PROMOTION_EXECUTION_COST_PCT,
            "non_overlapping_path_count": len(path_metrics),
            "profitable_non_overlapping_path_rate": profitable_path_rate,
            "non_overlapping_path_metrics": path_metrics,
            "actionable_filter_applied": actionable_filter_applied,
            "regime_filter_applied": regime_filter_applied,
        }
        if "source_ticker" in evaluation_table.columns:
            source_symbols = evaluation_table["source_ticker"].dropna().astype(str)
            if source_symbols.nunique() >= 3:
                per_ticker: dict[str, dict[str, Any]] = {}
                for symbol, ticker_frame in evaluation_table.groupby("source_ticker"):
                    per_ticker[str(symbol)] = ModelLifecycleService._historical_trading_quality_gate(
                        ticker_frame.drop(columns=["source_ticker"]),
                        target_name,
                    )
                pass_rate = sum(bool(item.get("passed")) for item in per_ticker.values()) / len(per_ticker)
                result["pooled_ticker_trading"] = per_ticker
                result["pooled_ticker_pass_rate"] = pass_rate
                if pass_rate < MIN_POOLED_TICKER_PASS_RATE:
                    result["reasons"].append("returns_not_robust_across_tickers")
                    result["passed"] = False
        return result

    def _upsert_registry(
        self,
        *,
        ticker: str,
        period: str,
        target_name: str,
        model_name: str,
        status: str,
        is_validated: bool,
        validation_score: float | None,
        stale_after_days: int,
        retrain_type: str | None,
        metrics_summary: dict,
        notes: str | None,
        last_trained_at_utc: str | None,
        last_evaluated_at_utc: str | None,
        last_promoted_at_utc: str | None = None,
        market: str = "US",
    ) -> None:
        clean_status = str(status).strip().lower()
        if clean_status not in MODEL_REGISTRY_STATUSES:
            raise ModelLifecycleError(f"Unsupported model status: {status}")

        now = _utc_now_iso()
        identity = resolve_model_identity(ticker, market)
        clean_market = identity.market
        clean_ticker = identity.ticker
        clean_model = str(model_name).strip().lower()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO market_model_registry (
                    market, ticker, period, target_name, model_name, status, is_validated,
                    validation_score, stale_after_days, retrain_type,
                    last_trained_at_utc, last_evaluated_at_utc, last_promoted_at_utc,
                    metrics_json, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market, ticker, period, target_name, model_name) DO UPDATE SET
                    status = excluded.status,
                    is_validated = excluded.is_validated,
                    validation_score = excluded.validation_score,
                    stale_after_days = excluded.stale_after_days,
                    retrain_type = excluded.retrain_type,
                    last_trained_at_utc = excluded.last_trained_at_utc,
                    last_evaluated_at_utc = excluded.last_evaluated_at_utc,
                    last_promoted_at_utc = COALESCE(excluded.last_promoted_at_utc, market_model_registry.last_promoted_at_utc),
                    metrics_json = excluded.metrics_json,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_market,
                    clean_ticker,
                    str(period).strip(),
                    str(target_name).strip(),
                    clean_model,
                    clean_status,
                    1 if is_validated else 0,
                    validation_score,
                    int(stale_after_days),
                    retrain_type,
                    last_trained_at_utc,
                    last_evaluated_at_utc,
                    last_promoted_at_utc,
                    json.dumps(metrics_summary or {}, ensure_ascii=False),
                    notes,
                    now,
                    now,
                ),
            )
            conn.commit()

    def _archive_other_production(
        self,
        *,
        ticker: str,
        period: str,
        target_name: str,
        keep_model_name: str,
        market: str = "US",
    ) -> None:
        identity = resolve_model_identity(ticker, market)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE market_model_registry
                SET status = 'archived',
                    notes = 'Superseded by a newer promoted production model.',
                    updated_at = ?
                WHERE market = ?
                  AND ticker = ?
                  AND period = ?
                  AND target_name = ?
                  AND status = 'production'
                  AND model_name <> ?
                """,
                (
                    _utc_now_iso(),
                    identity.market,
                    identity.ticker,
                    str(period).strip(),
                    str(target_name).strip(),
                    str(keep_model_name).strip().lower(),
                ),
            )
            conn.commit()

    def _row_to_registry_item(self, row: sqlite3.Row) -> dict[str, Any]:
        metrics_summary = _safe_json_load(row["metrics_json"], {})
        stale_after_days = int(row["stale_after_days"] or DEFAULT_STALE_DAYS)
        stored_is_validated = bool(int(row["is_validated"] or 0))
        gate_version = int(metrics_summary.get("validation_gate_version") or 0)
        validation_evidence_current = gate_version >= VALIDATION_GATE_VERSION
        return {
            "market": row["market"],
            "ticker": row["ticker"],
            "period": row["period"],
            "target_name": row["target_name"],
            "model_name": row["model_name"],
            "status": row["status"],
            "is_validated": stored_is_validated and validation_evidence_current,
            "stored_is_validated": stored_is_validated,
            "validation_gate_version": gate_version,
            "validation_evidence_current": validation_evidence_current,
            "validation_score": float(row["validation_score"]) if row["validation_score"] is not None else None,
            "stale_after_days": stale_after_days,
            "is_stale": self._is_stale(row["last_trained_at_utc"], stale_after_days),
            "retrain_type": row["retrain_type"],
            "last_trained_at_utc": row["last_trained_at_utc"],
            "last_evaluated_at_utc": row["last_evaluated_at_utc"],
            "last_promoted_at_utc": row["last_promoted_at_utc"],
            "metrics_summary": metrics_summary,
            "notes": row["notes"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_registry(
        self,
        *,
        ticker: str | None = None,
        period: str | None = None,
        target_name: str | None = None,
        market: str = "US",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clean_market = normalize_market(market)
        sql = "SELECT * FROM market_model_registry WHERE market = ?"
        params: list[Any] = [clean_market]
        if ticker:
            sql += " AND ticker = ?"
            params.append(resolve_model_identity(ticker, clean_market).ticker)
        if period:
            sql += " AND period = ?"
            params.append(str(period).strip())
        if target_name:
            sql += " AND target_name = ?"
            params.append(str(target_name).strip())
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_registry_item(row) for row in rows]

    def get_production_model(
        self,
        *,
        ticker: str,
        period: str = DEFAULT_PERIOD,
        target_name: str = DEFAULT_TARGET_NAME,
        market: str = "US",
    ) -> dict[str, Any] | None:
        identity = resolve_model_identity(ticker, market)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM market_model_registry
                WHERE market = ?
                  AND ticker = ?
                  AND period = ?
                  AND target_name = ?
                  AND status = 'production'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (identity.market, identity.ticker, str(period).strip(), str(target_name).strip()),
            ).fetchone()
        if row is None:
            return None
        item = self._row_to_registry_item(row)
        return item if item["is_validated"] else None

    def get_latest_validated_candidate(
        self,
        *,
        ticker: str,
        period: str = DEFAULT_PERIOD,
        target_name: str = DEFAULT_TARGET_NAME,
        market: str = "US",
    ) -> dict[str, Any] | None:
        identity = resolve_model_identity(ticker, market)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM market_model_registry
                WHERE market = ?
                  AND ticker = ?
                  AND period = ?
                  AND target_name = ?
                  AND status = 'candidate'
                  AND is_validated = 1
                ORDER BY validation_score DESC, updated_at DESC
                LIMIT 1
                """,
                (identity.market, identity.ticker, str(period).strip(), str(target_name).strip()),
            ).fetchone()
        return None if row is None else self._row_to_registry_item(row)

    def _promote_candidate_if_eligible(
        self,
        *,
        ticker: str,
        period: str,
        target_name: str,
        model_name: str,
        validation_score: float,
        market: str = "US",
    ) -> bool:
        if validation_score < PRODUCTION_MIN_SCORE:
            return False
        if target_name == OUTPERFORMANCE_TARGET_NAME:
            forward_gate = self._benchmark_forward_promotion_gate(
                ticker=ticker,
                period=period,
                model_name=model_name,
            )
            if not forward_gate["passed"]:
                logger.info(
                    "Benchmark-relative promotion deferred ticker=%s period=%s model=%s reasons=%s",
                    ticker,
                    period,
                    model_name,
                    forward_gate["reasons"],
                )
                return False

        current_prod = self.get_production_model(
            ticker=ticker,
            period=period,
            target_name=target_name,
            market=market,
        )
        prod_score = (
            float(current_prod["validation_score"])
            if current_prod and current_prod.get("validation_score") is not None
            else None
        )
        prod_stale = bool(current_prod and current_prod.get("is_stale"))

        should_promote = current_prod is None
        if not should_promote and prod_score is not None:
            should_promote = validation_score >= (prod_score + PROMOTION_DELTA)
        if not should_promote and prod_stale:
            should_promote = True

        if not should_promote:
            return False

        candidate_rows = self.list_registry(
            ticker=ticker,
            period=period,
            target_name=target_name,
            market=market,
            limit=20,
        )
        candidate_row = next(
            (
                row
                for row in candidate_rows
                if row["model_name"] == str(model_name).lower()
            ),
            None,
        )
        self._archive_other_production(
            ticker=ticker,
            period=period,
            target_name=target_name,
            keep_model_name=model_name,
            market=market,
        )
        self._upsert_registry(
            ticker=ticker,
            period=period,
            target_name=target_name,
            model_name=model_name,
            status="production",
            is_validated=True,
            validation_score=float(validation_score),
            stale_after_days=DEFAULT_STALE_DAYS,
            retrain_type="promotion",
            metrics_summary=dict(
                (candidate_row or {}).get("metrics_summary") or {}
            ),
            notes="Promoted by validation and observed outcome feedback.",
            last_trained_at_utc=(
                (candidate_row or {}).get("last_trained_at_utc")
                or _utc_now_iso()
            ),
            last_evaluated_at_utc=_utc_now_iso(),
            last_promoted_at_utc=_utc_now_iso(),
            market=market,
        )
        logger.info(
            "Promoted production model ticker=%s period=%s target=%s model=%s score=%.4f",
            ticker,
            period,
            target_name,
            model_name,
            validation_score,
        )
        return True

    def _benchmark_forward_promotion_gate(
        self,
        *,
        ticker: str,
        period: str,
        model_name: str,
    ) -> dict[str, Any]:
        """Require real forward evidence before benchmark-model promotion."""
        summary = self.feedback_service.get_benchmark_shadow_summary(
            ticker=ticker,
            model_period=period,
            model_name=model_name,
        )
        minimum_samples = int(get_settings().model_feedback_min_samples)
        sample_count = int(summary.get("sample_count") or 0)
        active_count = int(summary.get("active_signal_count") or 0)
        direction_accuracy = summary.get("direction_accuracy")
        average_net = summary.get("average_active_net_return_pct")
        profitable_rate = summary.get("active_profitable_rate")
        reasons: list[str] = []
        if sample_count < minimum_samples:
            reasons.append("insufficient_matured_forward_predictions")
        if active_count < MIN_FORWARD_ACTIVE_SIGNALS:
            reasons.append("insufficient_matured_active_signals")
        if direction_accuracy is None or float(direction_accuracy) < MIN_FORWARD_DIRECTION_ACCURACY:
            reasons.append("forward_direction_accuracy_below_minimum")
        if average_net is None or float(average_net) <= 0:
            reasons.append("forward_average_net_return_not_positive")
        if profitable_rate is None or float(profitable_rate) < MIN_FORWARD_PROFITABLE_RATE:
            reasons.append("forward_profitable_rate_below_minimum")
        return {
            **summary,
            "passed": not reasons,
            "reasons": reasons,
            "required_sample_count": minimum_samples,
            "required_active_signal_count": MIN_FORWARD_ACTIVE_SIGNALS,
            "required_direction_accuracy": MIN_FORWARD_DIRECTION_ACCURACY,
            "required_average_active_net_return_pct": "> 0",
            "required_active_profitable_rate": MIN_FORWARD_PROFITABLE_RATE,
        }

    def get_benchmark_forward_promotion_gate(
        self,
        *,
        ticker: str,
        period: str,
        model_name: str,
    ) -> dict[str, Any]:
        """Public read-only view of benchmark-model promotion readiness."""
        return self._benchmark_forward_promotion_gate(
            ticker=ticker,
            period=period,
            model_name=model_name,
        )

    def register_training_result(self, result: TrainingRunResult, retrain_type: str) -> dict[str, Any]:
        """Insert/refresh registry row for one training result and attempt promotion."""
        metrics_summary = dict(result.metrics or {})
        market = normalize_market(metrics_summary.get("market") or "US")
        task_type = str(metrics_summary.get("task_type", result.task_type)).lower()
        base_score = self._validation_score(
            metrics_summary=metrics_summary,
            task_type=task_type,
            target_name=result.target_name,
        )
        feedback_summary = self.feedback_service.get_model_summary(
            ticker=result.ticker,
            model_period=result.period,
            model_name=result.model_name,
            market=market,
        )
        score = self.feedback_service.blend_validation_with_feedback(
            validation_score=base_score,
            feedback_summary=feedback_summary,
        )
        metrics_summary["walk_forward_validation_score"] = base_score
        metrics_summary["live_feedback"] = feedback_summary
        metrics_summary["promotion_score"] = score
        quality_gate = self._walk_forward_quality_gate(result.evaluation_table)
        trading_gate = self._historical_trading_quality_gate(
            result.evaluation_table,
            result.target_name,
        )
        metrics_summary["walk_forward_quality_gate"] = quality_gate
        metrics_summary["historical_trading_quality_gate"] = trading_gate
        provenance_current = (
            int(metrics_summary.get("validation_scheme_version") or 0)
            >= MIN_VALIDATION_SCHEME_VERSION
            and int(metrics_summary.get("validation_gap_rows") or 0)
            >= TRADING_TARGET_HORIZON_ROWS
            and (
                result.target_name != TRADING_TARGET_NAME
                or (
                    bool(metrics_summary.get("stationary_features"))
                    and int(metrics_summary.get("feature_schema_version") or 0) >= 2
                )
            )
            and (
                result.target_name != OUTPERFORMANCE_TARGET_NAME
                or bool(
                    (metrics_summary.get("outperformance_economics_gate") or {}).get(
                        "passed"
                    )
                )
            )
            and (
                not bool(metrics_summary.get("pooled_training"))
                or (
                    "pooled_ticker_quality" in quality_gate
                    and (
                        result.target_name != TRADING_TARGET_NAME
                        or "pooled_ticker_trading" in trading_gate
                    )
                    and bool(metrics_summary.get("pooled_stationary_features"))
                    and int(metrics_summary.get("feature_schema_version") or 0) >= 2
                )
            )
        )
        metrics_summary["validation_gate_version"] = (
            VALIDATION_GATE_VERSION if provenance_current else 0
        )
        metrics_summary["validation_provenance_current"] = provenance_current
        forward_gate = (
            self._benchmark_forward_promotion_gate(
                ticker=result.ticker,
                period=result.period,
                model_name=result.model_name,
            )
            if result.target_name == OUTPERFORMANCE_TARGET_NAME
            else None
        )
        if forward_gate is not None:
            metrics_summary["benchmark_forward_promotion_gate"] = forward_gate
        validated = (
            provenance_current
            and
            score >= PRODUCTION_MIN_SCORE
            and bool(quality_gate["passed"])
            and bool(trading_gate["passed"])
        )

        self._upsert_registry(
            ticker=result.ticker,
            period=result.period,
            target_name=result.target_name,
            model_name=result.model_name,
            status="candidate",
            is_validated=validated,
            validation_score=score,
            stale_after_days=DEFAULT_STALE_DAYS,
            retrain_type=retrain_type,
            metrics_summary=metrics_summary,
            notes="Generated by automatic lifecycle training workflow.",
            last_trained_at_utc=metrics_summary.get("generated_at_utc") or _utc_now_iso(),
            last_evaluated_at_utc=_utc_now_iso(),
            market=market,
        )

        promoted = False
        if validated:
            promoted = self._promote_candidate_if_eligible(
                ticker=result.ticker,
                period=result.period,
                target_name=result.target_name,
                model_name=result.model_name,
                validation_score=score,
                market=market,
            )

        return {
            "market": market,
            "ticker": result.ticker,
            "period": result.period,
            "target_name": result.target_name,
            "model_name": result.model_name,
            "walk_forward_validation_score": base_score,
            "feedback_summary": feedback_summary,
            "quality_gate": quality_gate,
            "trading_quality_gate": trading_gate,
            "validation_score": score,
            "validated": validated,
            "promoted": promoted,
            "benchmark_forward_promotion_gate": forward_gate,
        }

    def sync_registry_from_saved_artifacts(self, limit: int = 600) -> int:
        """Discover saved artifacts and register them as candidates."""
        base_dir = Path(get_settings().research_models_dir)
        if not base_dir.exists():
            return 0

        files = sorted(
            {
                *base_dir.glob("*/*/*/*/metrics_summary.json"),
                *base_dir.glob("HK/*/*/*/*/metrics_summary.json"),
            },
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[: max(1, int(limit))]
        synced = 0
        for metrics_path in files:
            try:
                relative = metrics_path.relative_to(base_dir)
                if relative.parts[0] == "HK" and len(relative.parts) == 6:
                    market = "HK"
                    ticker, period, target_name, model_name, _ = relative.parts[1:]
                elif len(relative.parts) == 5:
                    market = "US"
                    ticker, period, target_name, model_name, _ = relative.parts
                else:
                    continue
                metrics_summary = _safe_json_load(metrics_path.read_text(encoding="utf-8"), {})
                task_type = str(metrics_summary.get("task_type", "")).lower()
                base_score = self._validation_score(
                    metrics_summary=metrics_summary,
                    task_type=task_type,
                    target_name=target_name,
                )
                feedback_summary = self.feedback_service.get_model_summary(
                    ticker=ticker,
                    model_period=period,
                    model_name=model_name,
                    market=market,
                )
                score = self.feedback_service.blend_validation_with_feedback(
                    validation_score=base_score,
                    feedback_summary=feedback_summary,
                )
                metrics_summary["walk_forward_validation_score"] = base_score
                metrics_summary["live_feedback"] = feedback_summary
                metrics_summary["promotion_score"] = score
                evaluation_path = metrics_path.parent / "evaluation_table.csv"
                evaluation_table = (
                    pd.read_csv(evaluation_path)
                    if evaluation_path.exists()
                    else None
                )
                quality_gate = self._walk_forward_quality_gate(evaluation_table)
                trading_gate = self._historical_trading_quality_gate(
                    evaluation_table,
                    target_name,
                )
                metrics_summary["walk_forward_quality_gate"] = quality_gate
                metrics_summary["historical_trading_quality_gate"] = trading_gate
                provenance_current = (
                    int(metrics_summary.get("validation_scheme_version") or 0)
                    >= MIN_VALIDATION_SCHEME_VERSION
                    and int(metrics_summary.get("validation_gap_rows") or 0)
                    >= TRADING_TARGET_HORIZON_ROWS
                    and (
                        target_name != TRADING_TARGET_NAME
                        or (
                            bool(metrics_summary.get("stationary_features"))
                            and int(metrics_summary.get("feature_schema_version") or 0) >= 2
                        )
                    )
                    and (
                        target_name != OUTPERFORMANCE_TARGET_NAME
                        or bool(
                            (metrics_summary.get("outperformance_economics_gate") or {}).get(
                                "passed"
                            )
                        )
                    )
                    and (
                        not bool(metrics_summary.get("pooled_training"))
                        or (
                            "pooled_ticker_quality" in quality_gate
                            and (
                                target_name != TRADING_TARGET_NAME
                                or "pooled_ticker_trading" in trading_gate
                            )
                            and bool(metrics_summary.get("pooled_stationary_features"))
                            and int(metrics_summary.get("feature_schema_version") or 0) >= 2
                        )
                    )
                )
                metrics_summary["validation_gate_version"] = (
                    VALIDATION_GATE_VERSION if provenance_current else 0
                )
                metrics_summary["validation_provenance_current"] = provenance_current
                if target_name == OUTPERFORMANCE_TARGET_NAME:
                    metrics_summary["benchmark_forward_promotion_gate"] = (
                        self._benchmark_forward_promotion_gate(
                            ticker=ticker,
                            period=period,
                            model_name=model_name,
                        )
                    )
                validated = (
                    provenance_current
                    and
                    score >= PRODUCTION_MIN_SCORE
                    and bool(quality_gate["passed"])
                    and bool(trading_gate["passed"])
                )
                existing_rows = self.list_registry(
                    ticker=ticker,
                    period=period,
                    target_name=target_name,
                    market=market,
                    limit=20,
                )
                existing = next(
                    (
                        row
                        for row in existing_rows
                        if row["model_name"] == str(model_name).lower()
                    ),
                    None,
                )
                self._upsert_registry(
                    ticker=ticker,
                    period=period,
                    target_name=target_name,
                    model_name=model_name,
                    status=(
                        "production"
                        if (
                            existing
                            and existing["status"] == "production"
                            and (
                                target_name != OUTPERFORMANCE_TARGET_NAME
                                or bool(
                                    metrics_summary.get(
                                        "benchmark_forward_promotion_gate",
                                        {},
                                    ).get("passed")
                                )
                            )
                        )
                        else "candidate"
                    ),
                    is_validated=validated,
                    validation_score=score,
                    stale_after_days=DEFAULT_STALE_DAYS,
                    retrain_type="artifact_sync",
                    metrics_summary=metrics_summary,
                    notes="Synced from saved artifact with outcome feedback.",
                    last_trained_at_utc=metrics_summary.get("generated_at_utc"),
                    last_evaluated_at_utc=_utc_now_iso(),
                    market=market,
                )
                synced += 1
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.warning("Model registry sync skipped path=%s error=%s", metrics_path, exc)
                continue
        return synced

    def _workflow_config(self, workflow_type: str) -> dict[str, Any]:
        clean_type = str(workflow_type).strip().lower()
        if clean_type not in MODEL_WORKFLOW_TYPES:
            raise ModelLifecycleError(f"Unsupported workflow type: {workflow_type}")
        if clean_type == "daily_incremental":
            return {"periods": ("2y",), "include_gradient": True, "universe_limit": 18, "pooled_limit": 8, "benchmark": "VOO"}
        if clean_type == "weekly_full":
            return {"periods": ("5y",), "include_gradient": True, "universe_limit": 35, "pooled_limit": 12, "benchmark": "VOO"}
        if clean_type == "monthly_deep":
            return {"periods": ("10y",), "include_gradient": True, "universe_limit": 55, "pooled_limit": 18, "benchmark": "VOO"}
        return {
            "periods": TRADING_MODEL_PERIODS,
            "include_gradient": True,
            "universe_limit": 6,
            "pooled_limit": 6,
            "benchmark": "VOO",
        }

    @staticmethod
    def _normalize_tickers(values: list[str], market: str = "US") -> list[str]:
        clean_market = normalize_market(market)
        seen: set[str] = set()
        output: list[str] = []
        for value in values:
            try:
                symbol = resolve_security(value, clean_market).ticker
            except ValueError:
                continue
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            output.append(symbol)
        return output

    def _insert_run_log_start(self, run_type: str, trigger_reason: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO model_lifecycle_runs(
                    run_type, trigger_reason, status, started_at_utc
                ) VALUES (?, ?, 'running', ?)
                """,
                (run_type, trigger_reason, _utc_now_iso()),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def _complete_run_log(
        self,
        *,
        run_id: int,
        status: str,
        processed_tickers: int,
        successful_models: int,
        failed_models: int,
        details: dict,
        error_message: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE model_lifecycle_runs
                SET status = ?,
                    completed_at_utc = ?,
                    processed_tickers = ?,
                    successful_models = ?,
                    failed_models = ?,
                    details_json = ?,
                    error_message = ?
                WHERE id = ?
                """,
                (
                    status,
                    _utc_now_iso(),
                    int(processed_tickers),
                    int(successful_models),
                    int(failed_models),
                    json.dumps(details or {}, ensure_ascii=False),
                    error_message,
                    int(run_id),
                ),
            )
            conn.commit()

    def list_recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM model_lifecycle_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            results.append(
                {
                    "id": int(row["id"]),
                    "run_type": row["run_type"],
                    "trigger_reason": row["trigger_reason"],
                    "status": row["status"],
                    "started_at_utc": row["started_at_utc"],
                    "completed_at_utc": row["completed_at_utc"],
                    "processed_tickers": int(row["processed_tickers"] or 0),
                    "successful_models": int(row["successful_models"] or 0),
                    "failed_models": int(row["failed_models"] or 0),
                    "details": _safe_json_load(row["details_json"], {}),
                    "error_message": row["error_message"],
                }
            )
        return results

    def run_training_workflow(
        self,
        *,
        workflow_type: str,
        trigger_reason: str,
        tickers: list[str] | None = None,
        market: str = "US",
    ) -> dict[str, Any]:
        """Run one automatic training workflow and update registry/promotion."""
        clean_market = normalize_market(market)
        config = self._workflow_config(workflow_type)
        run_id = self._insert_run_log_start(workflow_type, trigger_reason)

        if tickers is not None:
            source_tickers = tickers
        elif clean_market == "HK":
            # Always keep a small, diversified HK foundation so one user's
            # one-symbol watchlist cannot prevent pooled model formation. User
            # watchlists are then added, while the full HKEX list remains out
            # of scope for a resource-constrained deployment.
            from app.services.user_profile_service import (
                get_user_profile_store,
            )

            source_tickers = [
                *MARKET_CONFIGS[clean_market].default_tickers,
                *get_user_profile_store().list_effective_watchlist_tickers(
                    market="HK"
                ),
            ]
        else:
            source_tickers = get_active_universe(
                limit=int(config["universe_limit"])
            )
        universe = self._normalize_tickers(source_tickers, clean_market)
        if clean_market == "US":
            default_watchlist = self._normalize_tickers(
                get_settings().default_watchlist,
                clean_market,
            )
            universe = self._normalize_tickers(
                default_watchlist + universe,
                clean_market,
            )
        if not universe:
            self._complete_run_log(
                run_id=run_id,
                status="failed",
                processed_tickers=0,
                successful_models=0,
                failed_models=0,
                details={
                    "message": "No tickers available for model workflow.",
                    "market": clean_market,
                },
                error_message="No tickers available.",
            )
            raise ModelLifecycleError("No tickers available for model workflow.")

        successful_models = 0
        failed_models = 0
        per_ticker_errors: list[str] = []
        promotion_count = 0
        validated_count = 0
        rejected_count = 0
        rejection_reasons: Counter[str] = Counter()

        def record_outcome(outcome: dict[str, Any]) -> None:
            nonlocal successful_models, promotion_count, validated_count, rejected_count
            successful_models += 1
            if outcome.get("validated"):
                validated_count += 1
            else:
                rejected_count += 1
                rejection_reasons.update(
                    list((outcome.get("quality_gate") or {}).get("reasons") or [])
                    + list((outcome.get("trading_quality_gate") or {}).get("reasons") or [])
                )
            if outcome.get("promoted"):
                promotion_count += 1

        periods = tuple(str(item) for item in config["periods"])
        for ticker in universe:
            for training_period in periods:
                try:
                    results = train_baseline_models_for_ticker(
                        ticker=ticker,
                        period=training_period,
                        benchmark=MARKET_CONFIGS[clean_market].default_benchmark,
                        include_gradient_boosting=bool(config["include_gradient"]),
                        target_names=(TRADING_TARGET_NAME,),
                        market=clean_market,
                    )
                    for result in results:
                        outcome = self.register_training_result(result=result, retrain_type=workflow_type)
                        record_outcome(outcome)
                except Exception as exc:  # pragma: no cover - runtime guard
                    failed_models += 1
                    per_ticker_errors.append(f"{ticker}/{training_period}: {exc}")
                    logger.warning(
                        "Model lifecycle workflow skipped ticker=%s period=%s type=%s error=%s",
                        ticker,
                        training_period,
                        workflow_type,
                        exc,
                    )
                    continue

        pooled_universe = universe[: max(3, int(config.get("pooled_limit") or 3))]
        if len(pooled_universe) >= 3:
            for training_period in periods:
                try:
                    pooled_results = train_pooled_baseline_models(
                        tickers=pooled_universe,
                        period=training_period,
                        benchmark=MARKET_CONFIGS[clean_market].default_benchmark,
                        include_news_sentiment=False,
                        include_gradient_boosting=bool(config["include_gradient"]),
                        target_names=(TRADING_TARGET_NAME,),
                        market=clean_market,
                    )
                    for result in pooled_results:
                        record_outcome(
                            self.register_training_result(
                                result=result,
                                retrain_type=f"{workflow_type}_pooled",
                            )
                        )
                except Exception as exc:  # pragma: no cover - runtime guard
                    failed_models += 1
                    per_ticker_errors.append(
                        f"GLOBAL/{training_period}: {exc}"
                    )
                    logger.warning(
                        "Pooled model lifecycle workflow failed market=%s period=%s error=%s",
                        clean_market,
                        training_period,
                        exc,
                    )

        workflow_status = "success"
        if failed_models > 0 and successful_models > 0:
            workflow_status = "partial_success"
        elif successful_models == 0:
            workflow_status = "failed"

        details = {
            "market": clean_market,
            "periods": list(periods),
            "include_gradient": bool(config["include_gradient"]),
            "promotion_count": promotion_count,
            "validated_models": validated_count,
            "rejected_models": rejected_count,
            "rejection_reasons": dict(rejection_reasons.most_common()),
            "pooled_training_tickers": universe[: max(3, int(config.get("pooled_limit") or 3))],
            "errors": per_ticker_errors[:50],
        }
        self._complete_run_log(
            run_id=run_id,
            status=workflow_status,
            processed_tickers=len(universe),
            successful_models=successful_models,
            failed_models=failed_models,
            details=details,
            error_message=None if workflow_status != "failed" else "No models trained successfully.",
        )
        self.set_state(_state_key_for_market("last_retrain_time_utc", clean_market), _utc_now_iso())
        self.set_state(_state_key_for_market("last_workflow_type", clean_market), workflow_type)
        self.set_state(_state_key_for_market("last_workflow_status", clean_market), workflow_status)

        logger.info(
            "Model lifecycle workflow complete market=%s type=%s trigger=%s tickers=%d success=%d failed=%d promoted=%d",
            clean_market,
            workflow_type,
            trigger_reason,
            len(universe),
            successful_models,
            failed_models,
            promotion_count,
        )
        return self.list_recent_runs(limit=1)[0]

    def _detect_data_drift(self, ticker: str, market: str = "US") -> tuple[bool, float]:
        """Simple feature-drift check using rolling means (beginner baseline)."""
        try:
            df = build_feature_dataset(
                ticker=ticker,
                period="2y",
                benchmark=MARKET_CONFIGS[normalize_market(market)].default_benchmark,
                include_news_sentiment=False,
                market=market,
            ).sort_values("date")
        except Exception:
            return False, 0.0
        if df.empty or len(df) < 180:
            return False, 0.0

        cols = [
            column
            for column in ("close_return_1d", "rolling_volatility_20_pct", "distance_to_52w_high_pct")
            if column in df.columns
        ]
        if not cols:
            return False, 0.0

        baseline = df.iloc[-180:-45]
        recent = df.iloc[-45:]
        max_shift = 0.0
        for column in cols:
            b = baseline[column].astype(float).dropna()
            r = recent[column].astype(float).dropna()
            if len(b) < 20 or len(r) < 10:
                continue
            std = float(b.std()) if b.std() else 0.0
            if std <= 1e-9:
                continue
            shift = abs(float(r.mean()) - float(b.mean())) / std
            max_shift = max(max_shift, shift)
        return max_shift >= 2.5, max_shift

    def refresh_feedback_scores(self, limit: int = 300) -> dict[str, int]:
        """Refresh registry scores and promote reliable challengers."""
        rows = [
            row
            for market in ("US", "HK")
            for target in (TRADING_TARGET_NAME, OUTPERFORMANCE_TARGET_NAME)
            for row in self.list_registry(
                target_name=target,
                market=market,
                limit=max(1, int(limit)),
            )
            if row["status"] in {"candidate", "production"}
        ][: max(1, int(limit))]
        updated = 0
        promoted = 0
        for row in rows:
            metrics_summary = dict(row.get("metrics_summary") or {})
            base_score = float(
                metrics_summary.get("walk_forward_validation_score")
                or row.get("validation_score")
                or 0.0
            )
            feedback_summary = self.feedback_service.get_model_summary(
                ticker=row["ticker"],
                model_period=row["period"],
                model_name=row["model_name"],
                market=row.get("market") or "US",
            )
            combined_score = (
                base_score
                if row["target_name"] == OUTPERFORMANCE_TARGET_NAME
                else self.feedback_service.blend_validation_with_feedback(
                    validation_score=base_score,
                    feedback_summary=feedback_summary,
                )
            )
            metrics_summary["walk_forward_validation_score"] = base_score
            metrics_summary["live_feedback"] = feedback_summary
            metrics_summary["promotion_score"] = combined_score
            if row["target_name"] == OUTPERFORMANCE_TARGET_NAME:
                metrics_summary["benchmark_forward_promotion_gate"] = (
                    self._benchmark_forward_promotion_gate(
                        ticker=row["ticker"],
                        period=row["period"],
                        model_name=row["model_name"],
                    )
                )
            quality_gate = dict(
                metrics_summary.get("walk_forward_quality_gate") or {}
            )
            trading_gate = dict(
                metrics_summary.get("historical_trading_quality_gate") or {}
            )
            gate_version = int(metrics_summary.get("validation_gate_version") or 0)
            validated = (
                gate_version >= VALIDATION_GATE_VERSION
                and
                combined_score >= PRODUCTION_MIN_SCORE
                and bool(quality_gate.get("passed"))
                and bool(trading_gate.get("passed"))
            )
            effective_status = (
                "candidate"
                if (
                    row["target_name"] == OUTPERFORMANCE_TARGET_NAME
                    and not bool(
                        metrics_summary.get(
                            "benchmark_forward_promotion_gate",
                            {},
                        ).get("passed")
                    )
                )
                else row["status"]
            )
            self._upsert_registry(
                ticker=row["ticker"],
                period=row["period"],
                target_name=row["target_name"],
                model_name=row["model_name"],
                status=effective_status,
                is_validated=validated,
                validation_score=combined_score,
                stale_after_days=int(row["stale_after_days"]),
                retrain_type=row.get("retrain_type"),
                metrics_summary=metrics_summary,
                notes="Score refreshed from walk-forward and live outcomes.",
                last_trained_at_utc=row.get("last_trained_at_utc"),
                last_evaluated_at_utc=_utc_now_iso(),
                last_promoted_at_utc=row.get("last_promoted_at_utc"),
                market=row.get("market") or "US",
            )
            updated += 1
            if effective_status == "candidate" and validated:
                if self._promote_candidate_if_eligible(
                    ticker=row["ticker"],
                    period=row["period"],
                    target_name=row["target_name"],
                    model_name=row["model_name"],
                    validation_score=combined_score,
                    market=row.get("market") or "US",
                ):
                    promoted += 1
        return {"updated": updated, "promoted": promoted}

    def get_improvement_status(self) -> dict[str, Any]:
        """Summarize whether training evidence is progressing in each market."""
        markets: dict[str, Any] = {}
        for market in ("US", "HK"):
            rows = self.list_registry(
                target_name=TRADING_TARGET_NAME,
                market=market,
                limit=2000,
            )
            active = [row for row in rows if row["status"] in {"candidate", "production"}]
            current = [row for row in active if row["validation_evidence_current"]]
            validated = [row for row in current if row["is_validated"]]
            production = [row for row in validated if row["status"] == "production"]
            reasons: Counter[str] = Counter()
            for row in current:
                if row["is_validated"]:
                    continue
                metrics = dict(row.get("metrics_summary") or {})
                reasons.update(
                    list((metrics.get("walk_forward_quality_gate") or {}).get("reasons") or [])
                    + list((metrics.get("historical_trading_quality_gate") or {}).get("reasons") or [])
                )
            latest_training = max(
                (row.get("last_trained_at_utc") for row in active if row.get("last_trained_at_utc")),
                default=None,
            )
            markets[market] = {
                "market": market,
                "candidate_models": len(active),
                "current_evidence_models": len(current),
                "validated_models": len(validated),
                "production_models": len(production),
                "runtime_eligible_tickers": len({
                    row["ticker"]
                    for row in validated
                    if row["ticker"] != "GLOBAL" and not row["is_stale"]
                }),
                "pooled_models": sum(
                    row["ticker"] == "GLOBAL"
                    or bool((row.get("metrics_summary") or {}).get("pooled_training"))
                    for row in active
                ),
                "validated_pooled_models": sum(
                    row["ticker"] == "GLOBAL" and not row["is_stale"]
                    for row in validated
                ),
                "best_validation_score": max(
                    (float(row["validation_score"]) for row in validated if row.get("validation_score") is not None),
                    default=None,
                ),
                "latest_training_at_utc": latest_training,
                "top_rejection_reasons": dict(reasons.most_common(6)),
                "feedback": self.feedback_service.get_pipeline_status(market),
            }
        return {
            "validation_gate_version": VALIDATION_GATE_VERSION,
            "validation_scheme_version": TRAINING_VALIDATION_SCHEME_VERSION,
            "minimum_accepted_validation_scheme_version": MIN_VALIDATION_SCHEME_VERSION,
            "markets": markets,
        }

    def detect_retrain_triggers(
        self,
        max_models: int = 6,
        market: str = "US",
    ) -> list[str]:
        """Evaluate rolling-performance and drift triggers."""
        clean_market = normalize_market(market)
        production_rows = [
            row
            for row in self.list_registry(
                target_name=TRADING_TARGET_NAME,
                market=clean_market,
                limit=300,
            )
            if row["status"] == "production"
        ][: max(1, int(max_models))]
        triggers: list[str] = []

        for row in production_rows:
            ticker = row["ticker"]
            period = row["period"]
            model_name = row["model_name"]
            target_name = row["target_name"]

            feedback_summary = self.feedback_service.get_model_summary(
                ticker=ticker,
                model_period=period,
                model_name=model_name,
                market=clean_market,
            )
            if (
                int(feedback_summary.get("sample_count") or 0)
                >= get_settings().model_feedback_min_samples
                and float(feedback_summary.get("feedback_score") or 0.5) < 0.45
            ):
                triggers.append(
                    "live_feedback_weakened:"
                    f"{ticker}:{model_name}:"
                    f"{float(feedback_summary['feedback_score']):.3f}"
                )

            try:
                accuracy_payload = load_model_accuracy_summary(
                    ticker=ticker,
                    period=period,
                    target_name=target_name,
                    model_name=model_name,
                    window=20,
                    market=clean_market,
                )
                latest_rolling = accuracy_payload.get("latest_rolling_accuracy")
                if isinstance(latest_rolling, (int, float)) and float(latest_rolling) < 0.48:
                    triggers.append(
                        f"rolling_accuracy_drop:{ticker}:{model_name}:{float(latest_rolling):.3f}"
                    )
            except ModelResultsError:
                continue
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.debug("Trigger check accuracy skipped ticker=%s error=%s", ticker, exc)

            try:
                summary_payload = load_virtual_trader_summary(
                    ticker=ticker,
                    period=period,
                    model_name=model_name,
                    equity_limit=120,
                    market=clean_market,
                )
                summary = summary_payload.get("summary", {})
                outperformance = summary.get("outperformance_vs_benchmark_pct_points")
                if isinstance(outperformance, (int, float)) and float(outperformance) < -10.0:
                    triggers.append(
                        f"trading_performance_weakened:{ticker}:{model_name}:{float(outperformance):.2f}"
                    )
                observation_count = int(summary.get("risk_observation_count") or 0)
                max_drawdown = summary.get("max_drawdown_pct")
                if (
                    observation_count >= 60
                    and isinstance(max_drawdown, (int, float))
                    and float(max_drawdown) < -25.0
                ):
                    triggers.append(
                        f"trading_drawdown_exceeded:{ticker}:{model_name}:{float(max_drawdown):.2f}"
                    )
                sharpe_ratio = summary.get("sharpe_ratio")
                if (
                    observation_count >= 60
                    and isinstance(sharpe_ratio, (int, float))
                    and float(sharpe_ratio) < -0.25
                ):
                    triggers.append(
                        f"risk_adjusted_performance_weakened:{ticker}:{model_name}:{float(sharpe_ratio):.2f}"
                    )
            except Exception:
                # Historical virtual-trader artifacts may not always exist; keep trigger scan resilient.
                pass

            drift_detected, drift_score = self._detect_data_drift(
                ticker,
                market=clean_market,
            )
            if drift_detected:
                triggers.append(f"feature_drift_detected:{ticker}:z={drift_score:.2f}")

        deduped = list(dict.fromkeys(triggers))
        self.set_state(
            _state_key_for_market("last_active_triggers_json", clean_market),
            json.dumps(deduped, ensure_ascii=False),
        )
        return deduped

    def resolve_runtime_model_candidates(
        self,
        *,
        ticker: str,
        period: str,
        target_name: str = DEFAULT_TARGET_NAME,
        requested_model_name: str | None = None,
        periods: tuple[str, ...] | list[str] | None = None,
        market: str = "US",
    ) -> list[dict[str, Any]]:
        """Return validated runtime candidates ranked across training windows."""
        clean_market = normalize_market(market)
        clean_ticker = resolve_security(ticker, clean_market).ticker
        requested_periods = tuple(
            dict.fromkeys(
                str(item).strip()
                for item in (periods or (period,))
                if str(item).strip()
            )
        )
        clean_target = str(target_name).strip()
        attempts: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()

        registry_rows = self.list_registry(
            target_name=clean_target,
            market=clean_market,
            limit=1000,
        )
        eligible_rows = [
            row
            for row in registry_rows
            if row["period"] in requested_periods
            and row["ticker"] in {clean_ticker, "GLOBAL"}
            and row["status"] in {"production", "candidate"}
            and bool(row["is_validated"])
            and (clean_market != "HK" or not bool(row.get("is_stale")))
            and int(
                (row.get("metrics_summary") or {}).get("validation_gate_version") or 0
            ) >= VALIDATION_GATE_VERSION
        ]
        for row in eligible_rows:
            feedback_summary = self.feedback_service.get_model_summary(
                ticker=row["ticker"],
                model_period=row["period"],
                model_name=row["model_name"],
                market=clean_market,
            )
            metrics_summary = dict(row.get("metrics_summary") or {})
            base_score = float(
                metrics_summary.get("walk_forward_validation_score")
                or row.get("validation_score")
                or 0.0
            )
            row["feedback_summary"] = feedback_summary
            row["runtime_score"] = (
                self.feedback_service.blend_validation_with_feedback(
                    validation_score=base_score,
                    feedback_summary=feedback_summary,
                )
            )
        eligible_rows.sort(
            key=lambda row: (
                0 if row["ticker"] == clean_ticker else 1,
                bool(row.get("is_stale")),
                -(float(row.get("runtime_score") or 0.0)),
                0 if row["status"] == "production" else 1,
                requested_periods.index(row["period"]),
            )
        )
        for row in eligible_rows:
            key = (
                str(row["ticker"]).upper(),
                str(row["period"]),
                str(row["model_name"]).lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            attempts.append(
                {
                    "market": clean_market,
                    "ticker": key[0],
                    "period": key[1],
                    "model_name": key[2],
                    "source": (
                        "production_model"
                        if row["ticker"] == clean_ticker and row["status"] == "production"
                        else "validated_candidate"
                        if row["ticker"] == clean_ticker
                        else "shared_global_production"
                        if row["status"] == "production"
                        else "shared_global_candidate"
                    ),
                    "status": row["status"],
                    "is_stale": bool(row.get("is_stale", False)),
                    "validation_score": row.get("validation_score"),
                    "runtime_score": row.get("runtime_score"),
                    "feedback_summary": row.get("feedback_summary") or {},
                    "model_version": row.get("last_trained_at_utc") or "legacy",
                }
            )

        if requested_model_name:
            for requested_period in requested_periods:
                key = (
                    clean_ticker,
                    requested_period,
                    str(requested_model_name).strip().lower(),
                )
                if key not in seen:
                    attempts.append(
                        {
                            "market": clean_market,
                            "ticker": key[0],
                            "period": key[1],
                            "model_name": key[2],
                            "source": "requested_model",
                            "status": "requested",
                            "is_stale": False,
                            "validation_score": None,
                        }
                    )

        return attempts

    def get_recent_metrics(
        self,
        *,
        ticker: str,
        period: str,
        target_name: str,
        market: str = "US",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        rows = self.list_registry(
            ticker=ticker,
            period=period,
            target_name=target_name,
            market=market,
            limit=max(1, int(limit)),
        )
        metrics: list[dict[str, Any]] = []
        for row in rows:
            summary = dict(row.get("metrics_summary", {}))
            value = self._extract_metrics_value(summary)
            metrics.append(
                {
                    "market": row["market"],
                    "ticker": row["ticker"],
                    "model_name": row["model_name"],
                    "status": row["status"],
                    "validation_score": row.get("validation_score"),
                    "accuracy": value.get("accuracy"),
                    "f1": value.get("f1"),
                    "mae": value.get("mae"),
                    "rmse": value.get("rmse"),
                    "direction_accuracy": value.get("direction_accuracy"),
                    "generated_at_utc": summary.get("generated_at_utc"),
                }
            )
        return metrics


_SERVICE = ModelLifecycleService()


def get_model_lifecycle_service() -> ModelLifecycleService:
    """Return the shared model lifecycle service singleton."""
    return _SERVICE
