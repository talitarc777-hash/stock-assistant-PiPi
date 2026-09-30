"""Matched prospective evidence for return-model replacement.

This is a diagnostic long/cash policy, not a portfolio backtest. Existing
validation and account-level risk gates remain necessary.
"""
from __future__ import annotations

from collections import defaultdict
import json
import math
import numpy as np
from scipy.stats import t as student_t

MIN_NONOVERLAPPING_BLOCKS = 20


def _interval(values: list[float]) -> dict:
    mean = float(np.mean(values)) if values else 0.0
    if len(values) < 2:
        return {"mean": mean, "low": None, "high": None}
    margin = float(student_t.ppf(0.975, len(values) - 1) * np.std(values, ddof=1) / math.sqrt(len(values)))
    return {"mean": mean, "low": mean - margin, "high": mean + margin}


def compare_paired_rows(challenger: list[dict], incumbent: list[dict]) -> dict:
    def observations(rows):
        result = {}
        for row in sorted(rows, key=lambda r: r.get("recorded_at_utc", "")):
            if row.get("status") != "evaluated" or row.get("task_type") != "regression":
                continue
            outcome = row.get("outcome_date")
            recorded = row.get("recorded_at_utc")
            if not outcome or not recorded or recorded[:10] >= outcome:
                continue
            key = (row["ticker"], row["decision_date"], outcome, row["benchmark"], row["horizon_days"])
            result.setdefault(key, row)
        return result
    candidate, active = observations(challenger), observations(incumbent)
    daily = defaultdict(list)
    discarded = 0
    for key in sorted(candidate.keys() & active.keys()):
        left, right = candidate[key], active[key]
        try:
            price_l, price_r = float(left["decision_price"]), float(right["decision_price"])
            y, other_y = float(left["actual_return_pct"]), float(right["actual_return_pct"])
            p, q = float(left["prediction_value"]), float(right["prediction_value"])
            if not all(math.isfinite(v) for v in (price_l, price_r, y, other_y, p, q)):
                raise ValueError("nonfinite evidence")
            if price_l <= 0 or not math.isclose(price_l, price_r, rel_tol=1e-8) or not math.isclose(y, other_y, abs_tol=1e-6):
                raise ValueError("outcomes/prices do not match")
            # Equal costs for both forecasts: never reward shadow's zero quantity.
            costs = [max(0.1, float(json.loads(r.get("context_json") or "{}").get("comparison_cost_pct", 0.1)))
                     for r in (left, right)]
            cost = max(costs)
            if not math.isfinite(cost):
                raise ValueError("nonfinite cost")
        except (TypeError, ValueError, KeyError, AttributeError):
            discarded += 1
            continue
        cand_net = y - cost if p > max(1.0, cost) else 0.0
        active_net = y - cost if q > max(1.0, cost) else 0.0
        direction_delta = float((p > 0) == (y > 0)) - float((q > 0) == (y > 0))
        daily[key[1]].append((key[2], direction_delta, cand_net - active_net, cand_net))
    blocks = []
    previous_end = ""
    for day, values in sorted(daily.items()):
        if day <= previous_end:
            continue
        previous_end = max(v[0] for v in values)
        # Cross-sectional observations on the same day share a market shock.
        blocks.append([float(np.mean([v[i] for v in values])) for i in (1, 2, 3)])
    direction = _interval([v[0] for v in blocks])
    net = _interval([v[1] for v in blocks])
    reasons = []
    if len(blocks) < MIN_NONOVERLAPPING_BLOCKS:
        reasons.append("insufficient_matched_nonoverlapping_blocks")
    if direction["low"] is None or direction["low"] <= 0:
        reasons.append("paired_direction_improvement_not_established")
    if net["low"] is None or net["low"] <= 0:
        reasons.append("paired_net_improvement_not_established")
    if not blocks or np.mean([v[2] for v in blocks]) <= 0:
        reasons.append("challenger_long_cash_return_not_positive")
    return {
        "passed": not reasons, "reasons": reasons,
        "matched_rows": sum(map(len, daily.values())), "discarded_mismatches": discarded,
        "nonoverlapping_blocks": len(blocks), "minimum_blocks": MIN_NONOVERLAPPING_BLOCKS,
        "direction_delta_interval_95": direction, "net_delta_pct_interval_95": net,
        "policy": "same_cost_long_cash_1pct_band_v1", "is_portfolio_profit": False,
    }
