"""Read-only audit of saved models against the current lifecycle gates."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.model_lifecycle_service import (
    MAX_PROMOTION_PROXY_DRAWDOWN_PCT,
    MIN_ACTIVE_SIGNALS_PER_PATH,
    MIN_ACTIVE_SIGNAL_PROFITABLE_RATE,
    MIN_BALANCED_DIRECTION_ACCURACY,
    MIN_DIRECTION_ACCURACY,
    MIN_DIRECTION_EDGE,
    MIN_PROFITABLE_NON_OVERLAP_PATH_RATE,
    MIN_SIGNAL_MINORITY_RATE,
    MIN_WALK_FORWARD_ROWS,
    MIN_WORST_CLASS_RECALL,
    MIN_WORST_FOLD_ACCURACY,
    ModelLifecycleService,
    PRODUCTION_MIN_SCORE,
    MIN_VALIDATION_SCHEME_VERSION,
    TRADING_TARGET_HORIZON_ROWS,
    TRADING_TARGET_NAME,
    OUTPERFORMANCE_TARGET_NAME,
    VALIDATION_GATE_VERSION,
)
from app.services.ticker_classification import classify_ticker
from app.services.live_virtual_trader import (
    _assess_prediction_edge,
    _regression_prediction_confidence,
)


REPLAY_CONFIDENCE_THRESHOLD = 0.55
REPLAY_COST_PCT_PER_TRANSACTION = 0.05
MINIMUM_TRAINING_FOLDS = 2


def _safe_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _distribution(rows: list[dict[str, Any]], key: str) -> dict[str, float | int | None]:
    values = pd.to_numeric(
        pd.Series([row.get(key) for row in rows], dtype="object"),
        errors="coerce",
    ).dropna()
    if values.empty:
        return {"count": 0, "min": None, "p25": None, "median": None, "p75": None, "max": None}
    return {
        "count": int(len(values)),
        "min": float(values.min()),
        "p25": float(values.quantile(0.25)),
        "median": float(values.median()),
        "p75": float(values.quantile(0.75)),
        "max": float(values.max()),
    }


def _gate_distribution(
    rows: list[dict[str, Any]],
    key: str,
    threshold: float,
    *,
    comparison: str = "minimum",
) -> dict[str, float | int | str | None]:
    """Describe one gate's observed values and independent pass rate."""
    result: dict[str, float | int | str | None] = dict(_distribution(rows, key))
    values = pd.to_numeric(
        pd.Series([row.get(key) for row in rows], dtype="object"),
        errors="coerce",
    ).dropna()
    if comparison == "strict_minimum":
        passed = values > threshold
        operator = ">"
    elif comparison == "maximum":
        passed = values >= threshold
        operator = ">="
    else:
        passed = values >= threshold
        operator = ">="
    result.update(
        {
            "required_threshold": threshold,
            "comparison": operator,
            "pass_count": int(passed.sum()),
            "pass_rate": float(passed.mean()) if not values.empty else 0.0,
        }
    )
    return result


def _group_summary(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for value in sorted({str(row.get(key) or "unknown") for row in rows}):
        group = [row for row in rows if str(row.get(key) or "unknown") == value]
        passed = sum(bool(row.get("passed")) for row in group)
        behavioral = sum(bool(row.get("behavioral_gates_passed")) for row in group)
        statuses = Counter(str(row.get("audit_status") or "unknown") for row in group)
        output[value] = {
            "models": len(group),
            "currently_validated": passed,
            "behavioral_gates_passed": behavioral,
            "current_validation_rate": passed / len(group) if group else 0.0,
            "status_counts": dict(statuses),
        }
    return output


def _validation_funnels(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return both independent and genuinely cumulative validation counts."""
    independent = {
        "artifacts_scanned": len(rows),
        "evaluation_readable": sum(bool(row["evaluation_readable"]) for row in rows),
        "validation_score_passed": sum(
            bool(row["evaluation_readable"])
            and float(row.get("validation_score") or 0.0) >= PRODUCTION_MIN_SCORE
            for row in rows
        ),
        "walk_forward_quality_passed": sum(
            bool(row["walk_forward_quality_passed"]) for row in rows
        ),
        "historical_trading_quality_passed": sum(
            bool(row["historical_trading_quality_passed"]) for row in rows
        ),
        "validation_provenance_current": sum(
            bool(row["validation_provenance_current"]) for row in rows
        ),
        "all_gates_passed": sum(bool(row["passed"]) for row in rows),
    }
    readable_rows = [row for row in rows if bool(row["evaluation_readable"])]
    score_rows = [
        row for row in readable_rows
        if float(row.get("validation_score") or 0.0) >= PRODUCTION_MIN_SCORE
    ]
    quality_rows = [row for row in score_rows if bool(row["walk_forward_quality_passed"])]
    trading_rows = [
        row for row in quality_rows
        if bool(row["historical_trading_quality_passed"])
    ]
    provenance_rows = [
        row for row in trading_rows if bool(row["validation_provenance_current"])
    ]
    cumulative = {
        "artifacts_scanned": len(rows),
        "evaluation_readable": len(readable_rows),
        "plus_validation_score": len(score_rows),
        "plus_walk_forward_quality": len(quality_rows),
        "plus_historical_trading_quality": len(trading_rows),
        "plus_current_validation_provenance": len(provenance_rows),
        "all_gates_passed": sum(bool(row["passed"]) for row in provenance_rows),
    }
    return {
        "independent_gate_pass_counts": independent,
        "cumulative_gate_pass_counts": cumulative,
        "gate_order": [
            "evaluation_readable",
            "validation_score",
            "walk_forward_quality",
            "historical_trading_quality",
            "current_validation_provenance",
        ],
    }


def _counterfactual_replay(
    evaluation: pd.DataFrame | None,
    metrics: dict[str, Any],
    reliability: float,
) -> dict[str, Any]:
    """Replay the current prediction/edge layer on historical OOS timestamps.

    This is deliberately not presented as an account backtest: it excludes
    cash, board lots, position sizing, live context providers, and fixed HKD
    fees.  It answers the narrower question of how often the current confidence
    and hold-band policy would emit BUY/SELL/HOLD/SKIP from saved predictions.
    """
    if evaluation is None or evaluation.empty:
        return {"available": False, "reason": "evaluation_missing_or_empty"}
    required = {"predicted_value", "actual_future_result"}
    if not required.issubset(evaluation.columns):
        return {"available": False, "reason": "required_columns_missing"}
    work = evaluation.copy()
    work["predicted_value"] = pd.to_numeric(work["predicted_value"], errors="coerce")
    work["actual_future_result"] = pd.to_numeric(
        work["actual_future_result"], errors="coerce"
    )
    work = work.dropna(subset=["predicted_value", "actual_future_result"])
    if work.empty:
        return {"available": False, "reason": "no_finite_prediction_rows"}
    if "prediction_date" in work.columns:
        work = work.sort_values("prediction_date")
    def quantiles(values: list[float]) -> dict[str, float | int | None]:
        series = pd.Series(values, dtype="float64").replace([float("inf"), float("-inf")], pd.NA).dropna()
        if series.empty:
            return {"count": 0, "p1": None, "p5": None, "p25": None, "median": None, "p75": None, "p95": None, "p99": None}
        return {
            "count": int(len(series)),
            **{
                label: float(series.quantile(q))
                for label, q in (("p1", .01), ("p5", .05), ("p25", .25), ("median", .5), ("p75", .75), ("p95", .95), ("p99", .99))
            },
        }

    counts: Counter[str] = Counter()
    edge_reasons: Counter[str] = Counter()
    confidence_values: list[float] = []
    prediction_values: list[float] = []
    absolute_prediction_values: list[float] = []
    uncertainty_values: list[float] = []
    uncertainty_buffer_values: list[float] = []
    buy_threshold_values: list[float] = []
    after_cost_edge_values: list[float] = []
    buy_actual_returns: list[float] = []
    sell_actual_returns: list[float] = []
    active_direction_hits: list[float] = []
    path_net_returns: list[float] = []
    opposite_transitions = 0
    for path_number in range(TRADING_TARGET_HORIZON_ROWS):
        path = work.iloc[path_number::TRADING_TARGET_HORIZON_ROWS]
        holding = False
        wealth = 1.0
        previous_trade: str | None = None
        for _, row in path.iterrows():
            predicted = float(row["predicted_value"])
            prediction_values.append(predicted)
            absolute_prediction_values.append(abs(predicted))
            uncertainty = _regression_prediction_confidence(
                predicted_return_pct=predicted,
                metrics_summary=metrics,
                model_reliability=reliability,
            )
            confidence = uncertainty.get("confidence_score")
            if isinstance(confidence, (int, float)):
                confidence_values.append(float(confidence))
            if isinstance(uncertainty.get("out_of_sample_error_pct"), (int, float)):
                uncertainty_values.append(float(uncertainty["out_of_sample_error_pct"]))
            edge = _assess_prediction_edge(
                predicted_value=predicted,
                task_type="regression",
                confidence_score=confidence,
                confidence_threshold=REPLAY_CONFIDENCE_THRESHOLD,
                min_predicted_return_pct=0.0,
                estimated_transaction_cost_pct=REPLAY_COST_PCT_PER_TRANSACTION,
                uncertainty=uncertainty,
            )
            edge_reasons[str(edge["reason"])] += 1
            if isinstance(edge.get("uncertainty_buffer_pct"), (int, float)):
                uncertainty_buffer_values.append(float(edge["uncertainty_buffer_pct"]))
            if isinstance(edge.get("buy_threshold_pct"), (int, float)):
                buy_threshold_values.append(float(edge["buy_threshold_pct"]))
            after_cost_edge_values.append(abs(predicted) - REPLAY_COST_PCT_PER_TRANSACTION)
            regime_allowed = bool(row.get("is_regime_trade_allowed", True))
            action = "HOLD"
            cost = 0.0
            if bool(edge["bullish"]) and not holding:
                if regime_allowed:
                    action = "BUY"
                    holding = True
                    cost = REPLAY_COST_PCT_PER_TRANSACTION
                else:
                    action = "SKIP"
            elif bool(edge["bearish"]) and holding:
                action = "SELL"
                holding = False
                cost = REPLAY_COST_PCT_PER_TRANSACTION
            counts[action] += 1
            actual_return = float(row["actual_future_result"])
            if action == "BUY":
                buy_actual_returns.append(actual_return)
                active_direction_hits.append(float(actual_return > 0))
            elif action == "SELL":
                sell_actual_returns.append(actual_return)
                active_direction_hits.append(float(actual_return < 0))
            if action in {"BUY", "SELL"}:
                if previous_trade and previous_trade != action:
                    opposite_transitions += 1
                previous_trade = action
            interval_return = actual_return if holding else 0.0
            wealth *= max(0.0, 1.0 + (interval_return - cost) / 100.0)
        path_net_returns.append((wealth - 1.0) * 100.0)
    return {
        "available": True,
        "candidate_timestamp_count": int(len(work)),
        "decision_counts": dict(counts),
        "edge_reason_counts": dict(edge_reasons),
        "confidence_distribution": quantiles(confidence_values),
        "predicted_return_pct_distribution": quantiles(prediction_values),
        "absolute_predicted_return_pct_distribution": quantiles(absolute_prediction_values),
        "uncertainty_pct_distribution": quantiles(uncertainty_values),
        "uncertainty_buffer_pct_distribution": quantiles(uncertainty_buffer_values),
        "buy_threshold_pct_distribution": quantiles(buy_threshold_values),
        "absolute_edge_after_cost_pct_distribution": quantiles(after_cost_edge_values),
        "turnover_rate": (
            (counts["BUY"] + counts["SELL"]) / len(work) if len(work) else 0.0
        ),
        "average_realized_5d_return_after_buy_pct": (
            float(pd.Series(buy_actual_returns).mean()) if buy_actual_returns else None
        ),
        "average_realized_5d_return_after_sell_signal_pct": (
            float(pd.Series(sell_actual_returns).mean()) if sell_actual_returns else None
        ),
        "active_signal_direction_accuracy": (
            float(pd.Series(active_direction_hits).mean()) if active_direction_hits else None
        ),
        "non_overlapping_path_net_return_pct": path_net_returns,
        "median_non_overlapping_path_net_return_pct": (
            float(pd.Series(path_net_returns).median()) if path_net_returns else None
        ),
        "opposite_trade_transitions_at_or_after_horizon": opposite_transitions,
        "rapid_opposite_trade_transitions_inside_horizon": 0,
        "confidence_threshold": REPLAY_CONFIDENCE_THRESHOLD,
        "cost_pct_per_transaction": REPLAY_COST_PCT_PER_TRANSACTION,
        "limitations": (
            "Prediction-layer counterfactual only; excludes account cash, lot size, "
            "position sizing, live context, and fixed market-specific fees. Candidate "
            "models overlap and must not be summed into one portfolio result."
        ),
    }


def _predictive_quality_comparison(evaluation: pd.DataFrame | None) -> dict[str, Any]:
    """Compare one saved OOS prediction stream with time-safe simple baselines."""
    if evaluation is None or evaluation.empty:
        return {"available": False, "reason": "evaluation_missing_or_empty"}
    if not {"actual_future_result", "predicted_value"}.issubset(evaluation.columns):
        return {"available": False, "reason": "required_columns_missing"}
    work = evaluation.copy()
    actual = pd.to_numeric(work.get("actual_future_result"), errors="coerce")
    predicted = pd.to_numeric(work.get("predicted_value"), errors="coerce")
    valid = actual.notna() & predicted.notna()
    actual = actual[valid].reset_index(drop=True)
    predicted = predicted[valid].reset_index(drop=True)
    if len(actual) < 10:
        return {"available": False, "reason": "insufficient_rows"}

    def metrics(values: pd.Series) -> dict[str, Any]:
        values = pd.to_numeric(values, errors="coerce")
        keep = values.notna() & actual.notna()
        y = actual[keep]
        p = values[keep]
        actual_up = y > 0
        predicted_up = p > 0
        up_recall = float((predicted_up & actual_up).sum() / actual_up.sum()) if actual_up.any() else 0.0
        down = ~actual_up
        down_recall = float(((~predicted_up) & down).sum() / down.sum()) if down.any() else 0.0
        return {
            "sample_count": int(len(y)),
            "mae_pct": float((y - p).abs().mean()),
            "direction_accuracy": float((actual_up == predicted_up).mean()),
            "balanced_direction_accuracy": (up_recall + down_recall) / 2.0,
            "prediction_realized_correlation": (
                float(p.corr(y)) if len(y) > 1 and p.nunique() > 1 and y.nunique() > 1 else None
            ),
        }

    # A five-row lag ensures the target outcome was observable at prediction
    # time. The lagged target is also the just-completed prior five-day return,
    # making it a simple momentum baseline without reading future values.
    prior_five_day_return = actual.shift(TRADING_TARGET_HORIZON_ROWS).fillna(0.0)
    matured_history = actual.shift(TRADING_TARGET_HORIZON_ROWS)
    expanding_mean = matured_history.expanding(min_periods=1).mean().fillna(0.0)
    always_up = pd.Series([1.0] * len(actual))
    return {
        "available": True,
        "target_distribution": {
            "up": int((actual > 0).sum()),
            "down_or_flat": int((actual <= 0).sum()),
            "approximately_flat_abs_le_0_10_pct": int((actual.abs() <= 0.10).sum()),
        },
        "model": metrics(predicted),
        "zero_return": metrics(pd.Series([0.0] * len(actual))),
        "matured_historical_mean": metrics(expanding_mean),
        "previous_five_day_return_momentum": metrics(prior_five_day_return),
        "always_up_direction": metrics(always_up),
        "baseline_note": (
            "Historical mean and momentum use a five-row lag, so only outcomes "
            "already observable at the prediction timestamp are used."
        ),
    }


def audit_saved_models(
    models_dir: Path,
    *,
    target_name: str = TRADING_TARGET_NAME,
    tickers: set[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Evaluate artifacts without changing their files or the model registry."""
    rows: list[dict[str, Any]] = []
    failures: Counter[str] = Counter()
    paths = sorted({
        *models_dir.glob(f"*/*/{target_name}/*/metrics_summary.json"),
        *models_dir.glob(f"HK/*/*/{target_name}/*/metrics_summary.json"),
    })
    for metrics_path in paths:
        if "versions" in metrics_path.parts:
            continue
        relative = metrics_path.relative_to(models_dir)
        if relative.parts[0] == "HK" and len(relative.parts) == 6:
            market = "HK"
            ticker, period, artifact_target, model_name, _ = relative.parts[1:]
        elif len(relative.parts) == 5:
            market = "US"
            ticker, period, artifact_target, model_name, _ = relative.parts
        else:
            continue
        if tickers and ticker.upper() not in tickers:
            continue
        if limit is not None and len(rows) >= max(0, int(limit)):
            break

        metrics_summary = _safe_json(metrics_path)
        task_type = str(metrics_summary.get("task_type", "")).lower()
        score = ModelLifecycleService._validation_score(  # pylint: disable=protected-access
            object.__new__(ModelLifecycleService),
            metrics_summary,
            task_type,
            artifact_target,
        )
        evaluation_path = metrics_path.parent / "evaluation_table.csv"
        evaluation_readable = False
        try:
            evaluation = pd.read_csv(evaluation_path) if evaluation_path.exists() else None
            evaluation_readable = evaluation is not None and not evaluation.empty
        except (OSError, pd.errors.ParserError):
            evaluation = None
        quality = ModelLifecycleService._walk_forward_quality_gate(evaluation)  # pylint: disable=protected-access
        trading = ModelLifecycleService._historical_trading_quality_gate(  # pylint: disable=protected-access
            evaluation,
            artifact_target,
        )
        replay = (
            _counterfactual_replay(evaluation, metrics_summary, score)
            if artifact_target == TRADING_TARGET_NAME
            else {"available": False, "reason": "unsupported_target"}
        )
        predictive_quality = _predictive_quality_comparison(evaluation)
        behavioral_passed = (
            score >= PRODUCTION_MIN_SCORE
            and bool(quality.get("passed"))
            and bool(trading.get("passed"))
        )
        purging_current = (
            int(metrics_summary.get("validation_scheme_version") or 0)
            >= MIN_VALIDATION_SCHEME_VERSION
            and int(metrics_summary.get("validation_gap_rows") or 0)
            >= TRADING_TARGET_HORIZON_ROWS
        )
        provenance_current = (
            purging_current
            and (
                artifact_target != "target_5d_return"
                or (
                    bool(metrics_summary.get("stationary_features"))
                    and int(metrics_summary.get("feature_schema_version") or 0) >= 2
                )
            )
            and (
                artifact_target != OUTPERFORMANCE_TARGET_NAME
                or bool(
                    (metrics_summary.get("outperformance_economics_gate") or {}).get(
                        "passed"
                    )
                )
            )
            and (
                not bool(metrics_summary.get("pooled_training"))
                or (
                    "pooled_ticker_quality" in quality
                    and (
                        artifact_target != "target_5d_return"
                        or "pooled_ticker_trading" in trading
                    )
                    and bool(metrics_summary.get("pooled_stationary_features"))
                    and int(metrics_summary.get("feature_schema_version") or 0) >= 2
                )
            )
        )
        passed = behavioral_passed and provenance_current
        reasons = list(quality.get("reasons") or []) + list(trading.get("reasons") or [])
        if score < PRODUCTION_MIN_SCORE:
            reasons.append("validation_score_below_minimum")
        if not provenance_current:
            reasons.append(
                "unpurged_walk_forward_validation"
                if not purging_current
                else "validation_provenance_incomplete"
            )
        if (
            artifact_target == OUTPERFORMANCE_TARGET_NAME
            and not bool(
                (metrics_summary.get("outperformance_economics_gate") or {}).get("passed")
            )
        ):
            reasons.append("outperformance_economics_not_passed")
        failures.update(reasons)
        if passed:
            audit_status = "CURRENTLY_VALIDATED"
        elif behavioral_passed and not provenance_current:
            audit_status = "LEGACY_VALIDATION"
        elif not provenance_current:
            audit_status = "NEEDS_REVALIDATION"
        else:
            audit_status = "INVALID"
        if not evaluation_readable:
            primary_failure = "evaluation_missing_or_unreadable"
        elif not provenance_current:
            primary_failure = (
                "unpurged_walk_forward_validation"
                if not purging_current
                else "validation_provenance_incomplete"
            )
        elif score < PRODUCTION_MIN_SCORE:
            primary_failure = "validation_score_below_minimum"
        elif not bool(quality.get("passed")):
            primary_failure = str((quality.get("reasons") or ["walk_forward_quality_failed"])[0])
        elif not bool(trading.get("passed")):
            primary_failure = str((trading.get("reasons") or ["historical_trading_quality_failed"])[0])
        else:
            primary_failure = None
        rows.append(
            {
                "market": market,
                "ticker": ticker,
                "ticker_class": classify_ticker(ticker).primary_ticker_class,
                "period": period,
                "target_name": artifact_target,
                "model_name": model_name,
                "passed": passed,
                "audit_status": audit_status,
                "primary_failure": primary_failure,
                "evaluation_readable": evaluation_readable,
                "walk_forward_quality_passed": bool(quality.get("passed")),
                "historical_trading_quality_passed": bool(trading.get("passed")),
                "behavioral_gates_passed": behavioral_passed,
                "validation_provenance_current": provenance_current,
                "validation_score": score,
                "direction_accuracy": quality.get("direction_accuracy"),
                "direction_edge": quality.get("direction_edge"),
                "worst_fold_accuracy": quality.get("worst_fold_accuracy"),
                "signal_minority_rate": quality.get("signal_minority_rate"),
                "balanced_direction_accuracy": quality.get(
                    "balanced_direction_accuracy"
                ),
                "worst_class_recall": quality.get("worst_class_recall"),
                "positive_edge_non_overlapping_path_rate": quality.get(
                    "positive_edge_non_overlapping_path_rate"
                ),
                "effective_sample_count": quality.get("effective_non_overlapping_sample_count"),
                "evaluation_fold_count": (
                    int(evaluation["evaluation_window"].nunique(dropna=True))
                    if evaluation_readable and "evaluation_window" in evaluation.columns
                    else None
                ),
                "active_signal_count": trading.get("active_signal_count"),
                "median_net_active_return_pct": trading.get(
                    "average_active_return_pct_after_cost"
                ),
                "profitable_signal_rate": trading.get("profitable_signal_rate"),
                "profitable_non_overlapping_path_rate": trading.get(
                    "profitable_non_overlapping_path_rate"
                ),
                "worst_path_drawdown_pct": trading.get("max_signal_drawdown_pct"),
                "pooled_direction_ticker_pass_rate": quality.get("pooled_ticker_pass_rate"),
                "pooled_trading_ticker_pass_rate": trading.get("pooled_ticker_pass_rate"),
                "pooled_ticker_evidence": {
                    symbol: {
                        "direction_passed": bool(item.get("passed")),
                        "direction_accuracy": item.get("direction_accuracy"),
                        "direction_edge": item.get("direction_edge"),
                        "trading_passed": bool(
                            (trading.get("pooled_ticker_trading") or {}).get(symbol, {}).get("passed")
                        ),
                        "average_net_signal_return_pct": (
                            (trading.get("pooled_ticker_trading") or {})
                            .get(symbol, {})
                            .get("average_active_return_pct_after_cost")
                        ),
                        "max_drawdown_pct": (
                            (trading.get("pooled_ticker_trading") or {})
                            .get(symbol, {})
                            .get("max_signal_drawdown_pct")
                        ),
                    }
                    for symbol, item in (quality.get("pooled_ticker_quality") or {}).items()
                },
                "reasons": reasons,
                "counterfactual_replay": replay,
                "predictive_quality": predictive_quality,
            }
        )

    passed_rows = [row for row in rows if row["passed"]]
    ranking_key = lambda row: (
        float(row.get("validation_score") or 0.0),
        float(row.get("direction_edge") or 0.0),
        float(row.get("median_net_active_return_pct") or 0.0),
    )
    passed_rows.sort(
        key=lambda row: (
            float(row.get("median_net_active_return_pct") or 0.0),
            float(row.get("direction_edge") or 0.0),
        ),
        reverse=True,
    )
    failed_rows = [row for row in rows if not row["passed"]]
    failed_rows.sort(key=ranking_key, reverse=True)
    primary_failures = Counter(
        str(row["primary_failure"])
        for row in failed_rows
        if row.get("primary_failure")
    )
    audit_statuses = Counter(str(row["audit_status"]) for row in rows)
    replay_decisions: Counter[str] = Counter()
    replay_reasons: Counter[str] = Counter()
    replay_path_returns: list[float] = []
    replay_timestamps = replay_opposite_transitions = 0
    replay_buy_return_weighted = replay_sell_return_weighted = 0.0
    replay_buy_count = replay_sell_count = replay_active_hits_weighted = 0
    replay_distribution_rows: dict[str, list[dict[str, Any]]] = {
        "confidence_distribution": [],
        "predicted_return_pct_distribution": [],
        "absolute_predicted_return_pct_distribution": [],
        "uncertainty_pct_distribution": [],
        "uncertainty_buffer_pct_distribution": [],
        "buy_threshold_pct_distribution": [],
        "absolute_edge_after_cost_pct_distribution": [],
    }
    for row in rows:
        replay = dict(row.get("counterfactual_replay") or {})
        if not replay.get("available"):
            continue
        replay_decisions.update(replay.get("decision_counts") or {})
        replay_reasons.update(replay.get("edge_reason_counts") or {})
        replay_path_returns.extend(
            float(value)
            for value in (replay.get("non_overlapping_path_net_return_pct") or [])
        )
        replay_timestamps += int(replay.get("candidate_timestamp_count") or 0)
        replay_opposite_transitions += int(
            replay.get("opposite_trade_transitions_at_or_after_horizon") or 0
        )
        decisions = dict(replay.get("decision_counts") or {})
        buy_count = int(decisions.get("BUY") or 0)
        sell_count = int(decisions.get("SELL") or 0)
        if replay.get("average_realized_5d_return_after_buy_pct") is not None:
            replay_buy_return_weighted += buy_count * float(
                replay["average_realized_5d_return_after_buy_pct"]
            )
            replay_buy_count += buy_count
        if replay.get("average_realized_5d_return_after_sell_signal_pct") is not None:
            replay_sell_return_weighted += sell_count * float(
                replay["average_realized_5d_return_after_sell_signal_pct"]
            )
            replay_sell_count += sell_count
        active_count = buy_count + sell_count
        if replay.get("active_signal_direction_accuracy") is not None:
            replay_active_hits_weighted += int(round(
                active_count * float(replay["active_signal_direction_accuracy"])
            ))
        for key in replay_distribution_rows:
            distribution = dict(replay.get(key) or {})
            if int(distribution.get("count") or 0) > 0:
                replay_distribution_rows[key].append(distribution)
    validation_funnel = _validation_funnels(rows)
    return {
        "validation_gate_version": VALIDATION_GATE_VERSION,
        "target_name": target_name,
        "thresholds": {
            "validation_score": PRODUCTION_MIN_SCORE,
            "minimum_oos_rows": MIN_WALK_FORWARD_ROWS,
            "direction_accuracy": MIN_DIRECTION_ACCURACY,
            "worst_fold_accuracy": MIN_WORST_FOLD_ACCURACY,
            "direction_edge": MIN_DIRECTION_EDGE,
            "minority_signal_rate": MIN_SIGNAL_MINORITY_RATE,
            "balanced_direction_accuracy": MIN_BALANCED_DIRECTION_ACCURACY,
            "worst_class_recall": MIN_WORST_CLASS_RECALL,
            "active_profitable_rate": MIN_ACTIVE_SIGNAL_PROFITABLE_RATE,
            "worst_proxy_drawdown_pct": MAX_PROMOTION_PROXY_DRAWDOWN_PCT,
            "positive_non_overlap_path_rate": MIN_PROFITABLE_NON_OVERLAP_PATH_RATE,
            "minimum_training_folds": MINIMUM_TRAINING_FOLDS,
        },
        "models_scanned": len(rows),
        "models_passed": len(passed_rows),
        "pass_rate": (len(passed_rows) / len(rows)) if rows else 0.0,
        "failure_reasons": dict(failures.most_common()),
        "primary_failure_reasons": dict(primary_failures.most_common()),
        "audit_status_counts": dict(audit_statuses),
        "validation_funnel": validation_funnel,
        "metric_distributions": {
            key: _distribution(rows, key)
            for key in (
                "validation_score",
                "direction_accuracy",
                "direction_edge",
                "worst_fold_accuracy",
                "effective_sample_count",
                "median_net_active_return_pct",
                "profitable_non_overlapping_path_rate",
                "worst_path_drawdown_pct",
            )
        },
        "gate_metric_distributions": {
            "evaluation_fold_count": _gate_distribution(
                rows, "evaluation_fold_count", MINIMUM_TRAINING_FOLDS
            ),
            "effective_sample_count": _gate_distribution(
                rows, "effective_sample_count", MIN_WALK_FORWARD_ROWS
            ),
            "validation_score": _gate_distribution(
                rows, "validation_score", PRODUCTION_MIN_SCORE
            ),
            "direction_accuracy": _gate_distribution(
                rows, "direction_accuracy", MIN_DIRECTION_ACCURACY
            ),
            "direction_edge": _gate_distribution(
                rows, "direction_edge", MIN_DIRECTION_EDGE
            ),
            "worst_fold_accuracy": _gate_distribution(
                rows, "worst_fold_accuracy", MIN_WORST_FOLD_ACCURACY
            ),
            "signal_minority_rate": _gate_distribution(
                rows, "signal_minority_rate", MIN_SIGNAL_MINORITY_RATE
            ),
            "balanced_direction_accuracy": _gate_distribution(
                rows,
                "balanced_direction_accuracy",
                MIN_BALANCED_DIRECTION_ACCURACY,
            ),
            "worst_class_recall": _gate_distribution(
                rows, "worst_class_recall", MIN_WORST_CLASS_RECALL
            ),
            "positive_edge_non_overlapping_path_rate": _gate_distribution(
                rows,
                "positive_edge_non_overlapping_path_rate",
                MIN_PROFITABLE_NON_OVERLAP_PATH_RATE,
            ),
            "active_signal_count": _gate_distribution(
                rows, "active_signal_count", MIN_ACTIVE_SIGNALS_PER_PATH
            ),
            "median_net_active_return_pct": _gate_distribution(
                rows,
                "median_net_active_return_pct",
                0.0,
                comparison="strict_minimum",
            ),
            "profitable_signal_rate": _gate_distribution(
                rows,
                "profitable_signal_rate",
                MIN_ACTIVE_SIGNAL_PROFITABLE_RATE,
            ),
            "worst_path_drawdown_pct": _gate_distribution(
                rows,
                "worst_path_drawdown_pct",
                MAX_PROMOTION_PROXY_DRAWDOWN_PCT,
                comparison="maximum",
            ),
            "profitable_non_overlapping_path_rate": _gate_distribution(
                rows,
                "profitable_non_overlapping_path_rate",
                MIN_PROFITABLE_NON_OVERLAP_PATH_RATE,
            ),
        },
        "groups": {
            "market": _group_summary(rows, "market"),
            "model_name": _group_summary(rows, "model_name"),
            "period": _group_summary(rows, "period"),
            "ticker_class": _group_summary(rows, "ticker_class"),
        },
        "counterfactual_replay": {
            "candidate_model_timestamps": replay_timestamps,
            "decision_counts": dict(replay_decisions),
            "edge_reason_counts": dict(replay_reasons),
            "median_model_path_net_return_pct": (
                float(pd.Series(replay_path_returns).median())
                if replay_path_returns else None
            ),
            "opposite_trade_transitions_at_or_after_horizon": replay_opposite_transitions,
            "rapid_opposite_trade_transitions_inside_horizon": 0,
            "turnover_rate": (
                (int(replay_decisions.get("BUY", 0)) + int(replay_decisions.get("SELL", 0)))
                / replay_timestamps
                if replay_timestamps else 0.0
            ),
            "average_realized_5d_return_after_buy_pct": (
                replay_buy_return_weighted / replay_buy_count
                if replay_buy_count else None
            ),
            "average_realized_5d_return_after_sell_signal_pct": (
                replay_sell_return_weighted / replay_sell_count
                if replay_sell_count else None
            ),
            "active_signal_direction_accuracy": (
                replay_active_hits_weighted / (replay_buy_count + replay_sell_count)
                if replay_buy_count + replay_sell_count else None
            ),
            "median_model_distributions": {
                key: {
                    quantile: float(pd.Series([
                        item[quantile]
                        for item in distributions
                        if item.get(quantile) is not None
                    ]).median())
                    for quantile in ("p1", "p5", "p25", "median", "p75", "p95", "p99")
                }
                for key, distributions in replay_distribution_rows.items()
                if distributions
            },
            "confidence_threshold": REPLAY_CONFIDENCE_THRESHOLD,
            "cost_pct_per_transaction": REPLAY_COST_PCT_PER_TRANSACTION,
            "limitations": (
                "Aggregates alternative candidate models and is not a portfolio P&L. "
                "See each model row for five non-overlapping path results."
            ),
        },
        "passing_models": passed_rows,
        "strongest_failed_models": failed_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", default="data/models")
    parser.add_argument("--target", default=TRADING_TARGET_NAME)
    parser.add_argument("--tickers", default="", help="Comma-separated ticker filter.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()
    tickers = {item.strip().upper() for item in args.tickers.split(",") if item.strip()}
    report = audit_saved_models(
        Path(args.models_dir),
        target_name=args.target,
        tickers=tickers or None,
        limit=args.limit,
    )
    report["passing_models"] = report["passing_models"][: max(0, args.top)]
    report["strongest_failed_models"] = report["strongest_failed_models"][: max(0, args.top)]
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
