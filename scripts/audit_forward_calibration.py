"""Offline, chronological diagnostic of a fixed feedback calibration proposal.

Uses existing purged OOS predictions, never re-fits on test labels. The August
2025-2026 block was opened in earlier audits: this is NOT a new locked test.
No selection or promotion is performed. Models/windows/cohort are fixed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import balanced_accuracy_score, matthews_corrcoef


def delayed_correction(frame: pd.DataFrame) -> pd.Series:
    """Last 60 errors, >=30 matured observations, 6-row lag, capped at 1pp."""
    errors = frame["actual_future_result"] - frame["predicted_value"]
    correction = errors.shift(6).rolling(60, min_periods=30).mean().clip(-1.0, 1.0).fillna(0.0)
    return frame["predicted_value"] + correction


def metrics(frame: pd.DataFrame, prediction: str) -> dict:
    y = frame["actual_future_result"].to_numpy(float)
    p = frame[prediction].to_numpy(float)
    up = p > 0
    actual_up = y > 0
    active = p > 1.0  # Fixed 1% predicted-return band; not optimized.
    signals = np.where(active, y - 0.10, 0.0)  # 0.10pp round-trip diagnostic cost.
    return {
        "rows": len(y), "direction_accuracy": float(np.mean(up == actual_up)),
        "balanced_accuracy": float(balanced_accuracy_score(actual_up, up)),
        "mcc": float(matthews_corrcoef(actual_up, up)),
        "mae_pct": float(np.mean(np.abs(y - p))),
        "always_up_accuracy": float(actual_up.mean()),
        "buy_signal_count": int(active.sum()),
        "buy_hit_rate": float(np.mean(y[active] > 0.10)) if active.any() else None,
        "mean_net_return_per_opportunity_pct": float(signals.mean()),
        "always_in_asset_net_return_pct": float(np.mean(y - 0.10)),
    }


def run(root: Path) -> dict:
    streams = []
    paths = sorted(root.glob("*/2y/target_5d_return/ridge_regression/evaluation_table.csv"))
    paths += sorted(root.glob("HK/*/2y/target_5d_return/ridge_regression/evaluation_table.csv"))
    for path in paths:
        frame = pd.read_csv(path).sort_values("prediction_date").reset_index(drop=True)
        if frame["prediction_date"].duplicated().any():
            raise ValueError("Expected one OOS observation per ticker/date")
        frame["corrected"] = delayed_correction(frame)
        frame["market"] = "HK" if "HK" in path.relative_to(root).parts else "US"
        frame["path_phase"] = np.arange(len(frame)) % 5
        streams.append(frame)
    if not streams:
        raise ValueError("No archived 2y ridge OOS streams available")
    data = pd.concat(streams, ignore_index=True)
    results = {}
    for market in ("US", "HK"):
        sample = data[data.market == market]
        boundary = "2025-08-21" if market == "US" else "2025-08-13"
        dev_end = "2025-08-13" if market == "US" else "2025-08-05"
        for name, part in (("development", sample[sample.prediction_date <= dev_end]),
                           ("previously_opened_test", sample[sample.prediction_date >= boundary])):
            if part.empty:
                continue
            results[f"{market}_{name}"] = {
                "tickers": int(part.ticker.nunique()),
                "start": part.prediction_date.min(), "end": part.prediction_date.max(),
                "before": metrics(part, "predicted_value"), "after": metrics(part, "corrected"),
                "nonoverlap_paths": [
                    {"before": metrics(group, "predicted_value"), "after": metrics(group, "corrected")}
                    for _, group in part.groupby("path_phase")
                ],
            }
    return {
        "protocol": "Fixed 2y ridge cohort; 60-observation, 6-row-delayed residual correction capped at 1pp; no tuning or selection",
        "fresh_locked_test": False,
        "limitations": "Previously opened historical block. Not live accuracy, not independent ticker samples, not portfolio simulation. Signed-return targets cannot validate the full BUY/HOLD/SELL policy. HK fees/board lots are not modeled by this diagnostic.",
        "stream_count": len(streams), "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.source.resolve()):
        parser.error("Output must be outside the archived evidence tree")
    report = run(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: {k: v for k, v in row.items() if k != "nonoverlap_paths"}
                      for key, row in report["results"].items()}, indent=2))


if __name__ == "__main__":
    main()
