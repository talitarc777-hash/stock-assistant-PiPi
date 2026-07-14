"""Audit whether out-of-sample outperformance signals also made money after costs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.research_pipeline import (
    OUTPERFORMANCE_ROUND_TRIP_COST_PCT,
    build_feature_dataset,
)
from app.services.outperformance_economics import evaluate_outperformance_economics


def audit_outperformance_economics(
    evaluation_path: str | Path,
    *,
    ticker: str,
    period: str = "5y",
    benchmark: str = "VOO",
) -> dict:
    evaluation = pd.read_csv(evaluation_path)
    dataset = build_feature_dataset(
        ticker=ticker,
        period=period,
        benchmark=benchmark,
        include_news_sentiment=False,
    )
    economics = evaluate_outperformance_economics(
        evaluation,
        dataset,
        round_trip_cost_pct=OUTPERFORMANCE_ROUND_TRIP_COST_PCT,
    )
    return {
        "ticker": ticker.upper(),
        "benchmark": benchmark.upper(),
        **economics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evaluation_path")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--period", default="5y")
    parser.add_argument("--benchmark", default="VOO")
    args = parser.parse_args()
    payload = audit_outperformance_economics(
        args.evaluation_path,
        ticker=args.ticker,
        period=args.period,
        benchmark=args.benchmark,
    )
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
