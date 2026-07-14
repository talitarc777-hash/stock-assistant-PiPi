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
    ModelLifecycleService,
    PRODUCTION_MIN_SCORE,
    MIN_VALIDATION_SCHEME_VERSION,
    TRADING_TARGET_HORIZON_ROWS,
    TRADING_TARGET_NAME,
    OUTPERFORMANCE_TARGET_NAME,
    VALIDATION_GATE_VERSION,
)


def _safe_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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
    paths = sorted(models_dir.glob(f"*/*/{target_name}/*/metrics_summary.json"))
    for metrics_path in paths:
        relative = metrics_path.relative_to(models_dir)
        ticker, period, artifact_target, model_name, _ = relative.parts
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
        try:
            evaluation = pd.read_csv(evaluation_path) if evaluation_path.exists() else None
        except (OSError, pd.errors.ParserError):
            evaluation = None
        quality = ModelLifecycleService._walk_forward_quality_gate(evaluation)  # pylint: disable=protected-access
        trading = ModelLifecycleService._historical_trading_quality_gate(  # pylint: disable=protected-access
            evaluation,
            artifact_target,
        )
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
        rows.append(
            {
                "ticker": ticker,
                "period": period,
                "target_name": artifact_target,
                "model_name": model_name,
                "passed": passed,
                "behavioral_gates_passed": behavioral_passed,
                "validation_provenance_current": provenance_current,
                "validation_score": score,
                "direction_accuracy": quality.get("direction_accuracy"),
                "direction_edge": quality.get("direction_edge"),
                "worst_fold_accuracy": quality.get("worst_fold_accuracy"),
                "effective_sample_count": quality.get("effective_non_overlapping_sample_count"),
                "median_net_active_return_pct": trading.get(
                    "average_active_return_pct_after_cost"
                ),
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
    return {
        "validation_gate_version": VALIDATION_GATE_VERSION,
        "target_name": target_name,
        "models_scanned": len(rows),
        "models_passed": len(passed_rows),
        "pass_rate": (len(passed_rows) / len(rows)) if rows else 0.0,
        "failure_reasons": dict(failures.most_common()),
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
