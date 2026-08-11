"""Read saved model and virtual-trader artifacts for API responses."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import pickle
from typing import Any
from functools import lru_cache

import pandas as pd

from app.core.settings import get_settings
from app.services.market_config import model_security_root, normalize_market, resolve_security

logger = logging.getLogger(__name__)


class ModelResultsError(Exception):
    """Raised when saved model or simulation artifacts cannot be loaded."""


def _get_models_base_dir(base_dir: str | Path | None = None) -> Path:
    """Resolve the base directory containing saved research model artifacts."""
    return Path(base_dir or get_settings().research_models_dir)


@lru_cache(maxsize=128)
def _scan_saved_model_artifacts(
    period: str,
    target_name: str,
    base_dir_text: str,
    market: str = "US",
) -> list[dict[str, str]]:
    """Cache discovered saved model artifact directories for quick runtime reuse."""
    base_dir = Path(base_dir_text)
    discovered: list[dict[str, str]] = []
    if not base_dir.exists():
        return discovered
    clean_market = normalize_market(market)
    pattern = (
        f"*/{period}/{target_name}/*/model.pkl"
        if clean_market == "US"
        else f"{clean_market}/*/{period}/{target_name}/*/model.pkl"
    )
    for model_path in base_dir.glob(pattern):
        try:
            # model.pkl is stored as ticker/period/target/model/model.pkl.
            # The ticker is therefore four parent levels above the file.
            ticker = model_path.parent.parent.parent.parent.name.strip().upper()
            model_name = model_path.parent.name.strip().lower()
            if ticker and model_name:
                discovered.append(
                    {
                        "ticker": ticker,
                        "market": clean_market,
                        "model_name": model_name,
                        "artifact_dir": str(model_path.parent),
                    }
                )
        except Exception:
            continue
    return discovered


def list_compatible_saved_model_candidates(
    ticker: str,
    period: str = "5y",
    target_name: str = "target_5d_updown",
    requested_model_name: str | None = None,
    base_dir: str | Path | None = None,
    limit: int = 12,
    market: str = "US",
) -> list[dict[str, str]]:
    """Find compatible saved models before falling back to rules.

    Priority:
    1) exact ticker + requested model (if present)
    2) exact ticker + any model
    3) GLOBAL ticker + any model
    4) any ticker + requested model
    5) any ticker + any model
    """
    identity = resolve_security(ticker, market)
    clean_ticker = identity.ticker
    clean_period = str(period).strip()
    clean_target = str(target_name).strip()
    requested = str(requested_model_name).strip().lower() if requested_model_name else None
    base_path = _get_models_base_dir(base_dir)
    rows = _scan_saved_model_artifacts(
        clean_period,
        clean_target,
        str(base_path),
        identity.market,
    )
    if not rows:
        return []

    preferred_model_order = [
        "logistic_regression",
        "random_forest",
        "gradient_boosting",
        "linear_regression",
    ]
    rank_map = {name: idx for idx, name in enumerate(preferred_model_order)}

    def sort_key(row: dict[str, str]) -> tuple[int, int]:
        model_rank = rank_map.get(row["model_name"], 99)
        ticker_rank = 2
        if row["ticker"] == clean_ticker:
            ticker_rank = 0
        elif row["ticker"] == "GLOBAL":
            ticker_rank = 1
        return (ticker_rank, model_rank)

    seen: set[tuple[str, str]] = set()
    output: list[dict[str, str]] = []

    def append_row(row: dict[str, str], source: str) -> None:
        key = (row["ticker"], row["model_name"])
        if key in seen:
            return
        seen.add(key)
        output.append(
            {
                "ticker": row["ticker"],
                "model_name": row["model_name"],
                "source": source,
            }
        )

    if requested:
        for row in sorted(rows, key=sort_key):
            if row["ticker"] == clean_ticker and row["model_name"] == requested:
                append_row(row, "saved_exact_ticker_requested_model")
        if identity.market == "US":
            for row in sorted(rows, key=sort_key):
                if row["ticker"] != clean_ticker and row["model_name"] == requested:
                    append_row(row, "saved_compatible_requested_model")

    for row in sorted(rows, key=sort_key):
        if row["ticker"] == clean_ticker:
            append_row(row, "saved_exact_ticker_model")
    if identity.market == "HK":
        # HK models are always security-specific. Never reuse another HK
        # issuer's fitted model merely because its feature schema matches.
        return output[: max(1, int(limit))]
    for row in sorted(rows, key=sort_key):
        if row["ticker"] == "GLOBAL":
            append_row(row, "saved_global_model")
    for row in sorted(rows, key=sort_key):
        append_row(row, "saved_compatible_model")

    return output[: max(1, int(limit))]


def _resolve_model_artifact_dir(
    ticker: str,
    period: str = "5y",
    target_name: str = "target_5d_updown",
    model_name: str = "logistic_regression",
    base_dir: str | Path | None = None,
    market: str = "US",
) -> Path:
    """Resolve one saved model artifact directory."""
    identity = resolve_security(ticker, market)
    artifact_dir = model_security_root(
        _get_models_base_dir(base_dir), identity.market, identity.ticker
    ) / period / target_name / model_name
    if not artifact_dir.exists():
        raise ModelResultsError(
            "Saved model artifacts were not found for "
            f"ticker={ticker.strip().upper()} period={period} target={target_name} model={model_name}. "
            "Run the training command first."
        )
    return artifact_dir


def resolve_model_artifact_dir(
    ticker: str,
    period: str = "5y",
    target_name: str = "target_5d_updown",
    model_name: str = "logistic_regression",
    base_dir: str | Path | None = None,
    market: str = "US",
) -> Path:
    """Public wrapper for resolving one saved model artifact directory."""
    return _resolve_model_artifact_dir(
        ticker=ticker,
        period=period,
        target_name=target_name,
        model_name=model_name,
        base_dir=base_dir,
        market=market,
    )


def _resolve_virtual_trader_artifact_dir(
    ticker: str,
    period: str = "5y",
    model_name: str = "logistic_regression",
    base_dir: str | Path | None = None,
    market: str = "US",
) -> Path:
    """Resolve one saved virtual-trader artifact directory."""
    identity = resolve_security(ticker, market)
    artifact_dir = model_security_root(
        _get_models_base_dir(base_dir), identity.market, identity.ticker
    ) / period / "virtual_trader" / model_name
    if not artifact_dir.exists():
        raise ModelResultsError(
            "Saved virtual trader artifacts were not found for "
            f"ticker={ticker.strip().upper()} period={period} model={model_name}. "
            "Run the virtual trader command first."
        )
    return artifact_dir


def _read_json_file(path: Path) -> dict[str, Any]:
    """Read a JSON file into a dictionary."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ModelResultsError(f"Missing artifact file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ModelResultsError(f"Invalid JSON in artifact file: {path}") from exc


def _read_csv_file(path: Path) -> pd.DataFrame:
    """Read a CSV file into a DataFrame."""
    try:
        return pd.read_csv(path)
    except FileNotFoundError as exc:
        raise ModelResultsError(f"Missing artifact file: {path}") from exc
    except Exception as exc:
        raise ModelResultsError(f"Failed to read CSV artifact: {path}") from exc


def load_trained_model_bundle(
    ticker: str,
    period: str = "5y",
    target_name: str = "target_5d_updown",
    model_name: str = "logistic_regression",
    base_dir: str | Path | None = None,
    market: str = "US",
) -> dict[str, Any]:
    """Load trained model object + feature list from saved artifacts."""
    artifact_dir = _resolve_model_artifact_dir(
        ticker=ticker,
        period=period,
        target_name=target_name,
        model_name=model_name,
        base_dir=base_dir,
        market=market,
    )

    model_path = artifact_dir / "model.pkl"
    feature_list_path = artifact_dir / "feature_list.json"
    metrics_path = artifact_dir / "metrics_summary.json"

    try:
        with model_path.open("rb") as handle:
            model = pickle.load(handle)
    except FileNotFoundError as exc:
        raise ModelResultsError(f"Missing artifact file: {model_path}") from exc
    except Exception as exc:
        raise ModelResultsError(f"Failed to load trained model artifact: {model_path}") from exc

    metrics = _read_json_file(metrics_path)
    try:
        feature_names = json.loads(feature_list_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ModelResultsError(f"Missing artifact file: {feature_list_path}") from exc
    except Exception as exc:
        raise ModelResultsError(f"Failed to read feature list artifact: {feature_list_path}") from exc

    if not isinstance(feature_names, list) or not all(isinstance(item, str) for item in feature_names):
        raise ModelResultsError("Invalid feature list format in saved artifacts.")

    return {
        "artifact_dir": artifact_dir,
        "model": model,
        "feature_names": feature_names,
        "task_type": str(metrics.get("task_type", "classification")),
        "target_name": str(metrics.get("target_name", target_name)),
        "metrics": metrics,
    }


def _build_rolling_accuracy_series(evaluation_df: pd.DataFrame, window: int) -> pd.DataFrame:
    """Compute a rolling hit-rate series from the evaluation table."""
    if window < 1:
        raise ModelResultsError("Rolling accuracy window must be >= 1.")

    if evaluation_df.empty:
        return pd.DataFrame(columns=["date", "rolling_accuracy"])

    work_df = evaluation_df.copy()
    work_df["prediction_date"] = pd.to_datetime(work_df["prediction_date"], errors="coerce")
    work_df = work_df.dropna(subset=["prediction_date"]).sort_values("prediction_date").reset_index(drop=True)
    work_df["hit_value"] = (work_df["hit_miss"].astype(str).str.lower() == "hit").astype(int)
    work_df["rolling_accuracy"] = work_df["hit_value"].rolling(window=window, min_periods=1).mean()

    return pd.DataFrame(
        {
            "date": work_df["prediction_date"].dt.strftime("%Y-%m-%d"),
            "rolling_accuracy": work_df["rolling_accuracy"],
        }
    )


def load_model_latest_prediction(
    ticker: str,
    period: str = "5y",
    target_name: str = "target_5d_updown",
    model_name: str = "logistic_regression",
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Load the latest prediction row from the saved evaluation table."""
    artifact_dir = _resolve_model_artifact_dir(
        ticker=ticker,
        period=period,
        target_name=target_name,
        model_name=model_name,
        base_dir=base_dir,
    )
    evaluation_df = _read_csv_file(artifact_dir / "evaluation_table.csv")
    if evaluation_df.empty:
        raise ModelResultsError("Evaluation table is empty.")

    latest_row = evaluation_df.sort_values("prediction_date").iloc[-1].to_dict()
    return {
        "ticker": ticker.strip().upper(),
        "period": period,
        "target_name": target_name,
        "model_name": model_name,
        "latest": latest_row,
    }


def load_model_history(
    ticker: str,
    period: str = "5y",
    target_name: str = "target_5d_updown",
    model_name: str = "logistic_regression",
    limit: int = 200,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Load model prediction history and a rolling-accuracy chart table."""
    artifact_dir = _resolve_model_artifact_dir(
        ticker=ticker,
        period=period,
        target_name=target_name,
        model_name=model_name,
        base_dir=base_dir,
    )
    evaluation_df = _read_csv_file(artifact_dir / "evaluation_table.csv")
    if evaluation_df.empty:
        raise ModelResultsError("Evaluation table is empty.")

    evaluation_df = evaluation_df.sort_values("prediction_date").reset_index(drop=True)
    rolling_accuracy_df = _build_rolling_accuracy_series(evaluation_df, window=20)
    if limit > 0:
        evaluation_df = evaluation_df.tail(limit).reset_index(drop=True)
        rolling_accuracy_df = rolling_accuracy_df.tail(limit).reset_index(drop=True)

    return {
        "ticker": ticker.strip().upper(),
        "period": period,
        "target_name": target_name,
        "model_name": model_name,
        "count": len(evaluation_df),
        "history": evaluation_df.to_dict(orient="records"),
        "rolling_accuracy": rolling_accuracy_df.to_dict(orient="records"),
    }


def load_model_accuracy_summary(
    ticker: str,
    period: str = "5y",
    target_name: str = "target_5d_updown",
    model_name: str = "logistic_regression",
    window: int = 20,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Load saved model metrics plus a rolling-accuracy series."""
    artifact_dir = _resolve_model_artifact_dir(
        ticker=ticker,
        period=period,
        target_name=target_name,
        model_name=model_name,
        base_dir=base_dir,
    )
    metrics = _read_json_file(artifact_dir / "metrics_summary.json")
    evaluation_df = _read_csv_file(artifact_dir / "evaluation_table.csv")
    rolling_accuracy_df = _build_rolling_accuracy_series(evaluation_df, window=window)

    latest_rolling_accuracy = None
    if not rolling_accuracy_df.empty:
        latest_rolling_accuracy = float(rolling_accuracy_df.iloc[-1]["rolling_accuracy"])

    return {
        "ticker": ticker.strip().upper(),
        "period": period,
        "target_name": target_name,
        "model_name": model_name,
        "metrics": metrics,
        "latest_rolling_accuracy": latest_rolling_accuracy,
        "rolling_accuracy": rolling_accuracy_df.to_dict(orient="records"),
    }


def load_model_evaluation_table(
    ticker: str,
    period: str = "5y",
    target_name: str = "target_5d_updown",
    model_name: str = "logistic_regression",
    base_dir: str | Path | None = None,
) -> pd.DataFrame:
    """Load the full saved walk-forward evaluation table for one model."""
    artifact_dir = _resolve_model_artifact_dir(
        ticker=ticker,
        period=period,
        target_name=target_name,
        model_name=model_name,
        base_dir=base_dir,
    )
    evaluation_df = _read_csv_file(artifact_dir / "evaluation_table.csv")
    if evaluation_df.empty:
        raise ModelResultsError("Evaluation table is empty.")
    return evaluation_df.sort_values("prediction_date").reset_index(drop=True)


def load_virtual_trader_summary(
    ticker: str,
    period: str = "5y",
    model_name: str = "logistic_regression",
    equity_limit: int = 500,
    base_dir: str | Path | None = None,
    market: str = "US",
) -> dict[str, Any]:
    """Load saved virtual trader summary, benchmark comparison, and equity curve."""
    artifact_dir = _resolve_virtual_trader_artifact_dir(
        ticker=ticker,
        period=period,
        model_name=model_name,
        base_dir=base_dir,
        market=market,
    )
    summary = _read_json_file(artifact_dir / "summary.json")
    benchmark_comparison = _read_json_file(artifact_dir / "benchmark_comparison.json")
    equity_curve_df = _read_csv_file(artifact_dir / "equity_curve.csv")

    if equity_limit > 0:
        equity_curve_df = equity_curve_df.tail(equity_limit).reset_index(drop=True)

    return {
        "ticker": ticker.strip().upper(),
        "period": period,
        "model_name": model_name,
        "summary": summary,
        "benchmark_comparison": benchmark_comparison,
        "equity_curve": equity_curve_df.to_dict(orient="records"),
    }


def load_virtual_trader_trades(
    ticker: str,
    period: str = "5y",
    model_name: str = "logistic_regression",
    limit: int = 200,
    base_dir: str | Path | None = None,
    market: str = "US",
) -> dict[str, Any]:
    """Load saved virtual trader trade log and contribution history."""
    artifact_dir = _resolve_virtual_trader_artifact_dir(
        ticker=ticker,
        period=period,
        model_name=model_name,
        base_dir=base_dir,
        market=market,
    )
    trade_log_df = _read_csv_file(artifact_dir / "trade_log.csv")
    contribution_df = _read_csv_file(artifact_dir / "monthly_contributions.csv")

    if limit > 0:
        trade_log_df = trade_log_df.tail(limit).reset_index(drop=True)
        contribution_df = contribution_df.tail(limit).reset_index(drop=True)

    return {
        "ticker": ticker.strip().upper(),
        "period": period,
        "model_name": model_name,
        "trade_count": len(trade_log_df),
        "trade_log": trade_log_df.to_dict(orient="records"),
        "monthly_contributions": contribution_df.to_dict(orient="records"),
    }
