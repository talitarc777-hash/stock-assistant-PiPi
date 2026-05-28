"""Market data service for downloading and cleaning daily OHLCV history."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class MarketDataError(Exception):
    """Base exception for market data service errors."""


class InvalidTickerError(MarketDataError):
    """Raised when ticker input is missing or invalid."""


class EmptyDataError(MarketDataError):
    """Raised when a data provider returns no rows for the request."""


# Dependency-injection friendly function signature for tests.
DownloadFn = Callable[[str, str], pd.DataFrame]

_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "GIT_HTTP_PROXY",
    "GIT_HTTPS_PROXY",
)


def _clear_broken_local_proxy_env() -> None:
    """Clear known-invalid local proxy values that break yfinance requests."""
    broken_markers = ("127.0.0.1:9", "localhost:9")
    for key in _PROXY_ENV_KEYS:
        value = os.environ.get(key, "")
        if value and any(marker in value for marker in broken_markers):
            os.environ.pop(key, None)


def _default_download_daily(ticker: str, period: str) -> pd.DataFrame:
    """
    Download daily price history with yfinance.

    Args:
        ticker: Instrument symbol like "VOO" or "AAPL".
        period: yfinance period string like "1y", "5y", or "max".
    """
    _clear_broken_local_proxy_env()
    cache_dir = Path("data") / "yfinance_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        yf.set_tz_cache_location(str(cache_dir))
    except Exception:
        # Best-effort only. If unavailable in this yfinance version,
        # requests may still succeed with the default cache location.
        pass

    try:
        primary = yf.download(
            tickers=ticker,
            period=period,
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        if primary is not None and not primary.empty:
            return primary
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("yf.download failed for ticker=%s period=%s: %s", ticker, period, exc)

    # Some yfinance/pandas combinations intermittently fail with yf.download.
    # Fallback to Ticker.history to improve reliability in local environments.
    fallback = yf.Ticker(ticker).history(
        period=period,
        interval="1d",
        auto_adjust=False,
    )
    return fallback


def _validate_ticker(ticker: str) -> str:
    """
    Validate and normalize a ticker symbol.

    Returns:
        Uppercased ticker symbol.
    """
    normalized: str = ticker.strip().upper()
    if not normalized:
        raise InvalidTickerError("Ticker is required and cannot be blank.")
    return normalized


def _clean_ohlcv_dataframe(raw_df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """
    Convert raw yfinance output into a clean, predictable DataFrame.

    Output columns:
        date, open, high, low, close, adj_close, volume
    """
    if raw_df is None or raw_df.empty:
        raise EmptyDataError("No price data returned for this request.")

    # Newer yfinance versions may return MultiIndex columns even for a single ticker,
    # e.g. columns like ("Close", "VOO"). Normalize that to flat columns.
    if isinstance(raw_df.columns, pd.MultiIndex):
        # Try to select the requested ticker from the column levels.
        level_values = [set(raw_df.columns.get_level_values(i)) for i in range(raw_df.columns.nlevels)]
        if ticker in level_values[-1]:
            raw_df = raw_df.xs(ticker, axis=1, level=raw_df.columns.nlevels - 1, drop_level=True)
        elif raw_df.columns.nlevels >= 2 and ticker in level_values[1]:
            raw_df = raw_df.xs(ticker, axis=1, level=1, drop_level=True)
        else:
            # Fallback: flatten by taking the first element of each tuple.
            raw_df = raw_df.copy()
            raw_df.columns = [col[0] for col in raw_df.columns]

    original_index = raw_df.index

    # yfinance index usually contains Date; we convert it to a normal column.
    clean_df: pd.DataFrame = raw_df.reset_index()

    # yfinance/pandas may use Date, Datetime, or a generic name like "index"
    # when the DatetimeIndex is unnamed. Normalize all valid variants.
    if "Date" in clean_df.columns:
        clean_df = clean_df.rename(columns={"Date": "date"})
    elif "Datetime" in clean_df.columns:
        clean_df = clean_df.rename(columns={"Datetime": "date"})
    elif "index" in clean_df.columns and pd.api.types.is_datetime64_any_dtype(original_index):
        clean_df = clean_df.rename(columns={"index": "date"})
    elif "level_0" in clean_df.columns and pd.api.types.is_datetime64_any_dtype(original_index):
        clean_df = clean_df.rename(columns={"level_0": "date"})
    else:
        date_like_columns = [
            column
            for column in clean_df.columns
            if pd.api.types.is_datetime64_any_dtype(clean_df[column])
        ]
        if date_like_columns:
            clean_df = clean_df.rename(columns={date_like_columns[0]: "date"})
        else:
            raise MarketDataError(
                "Unexpected response format: missing date column. "
                f"Received columns: {[str(column) for column in clean_df.columns]}."
            )

    expected_columns: dict[str, str] = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    }

    missing_columns: list[str] = [
        source_col for source_col in expected_columns if source_col not in clean_df.columns
    ]
    if missing_columns:
        raise MarketDataError(
            f"Unexpected response format: missing columns {missing_columns}."
        )

    # Keep only expected columns in desired order, then rename to snake_case.
    clean_df = clean_df[["date", *expected_columns.keys()]].rename(columns=expected_columns)

    # Normalize dtypes and sort by ascending date.
    clean_df["date"] = pd.to_datetime(clean_df["date"], errors="coerce")
    numeric_columns = ["open", "high", "low", "close", "adj_close", "volume"]
    for column in numeric_columns:
        clean_df[column] = pd.to_numeric(clean_df[column], errors="coerce")
    clean_df = clean_df.dropna(subset=["date", "close"]).sort_values(by="date").reset_index(drop=True)

    if clean_df.empty:
        raise EmptyDataError("No valid rows after cleaning price history.")

    return clean_df


def get_price_history(
    ticker: str,
    period: str = "5y",
    download_fn: DownloadFn | None = None,
) -> pd.DataFrame:
    """
    Fetch and clean daily OHLCV data for a single ticker.

    Args:
        ticker: Instrument symbol (e.g., "VOO", "MSFT").
        period: Lookback period for yfinance (default "5y").
        download_fn: Optional custom downloader for unit testing.

    Returns:
        pandas DataFrame with columns:
        date, open, high, low, close, adj_close, volume
    """
    safe_ticker: str = _validate_ticker(ticker)
    downloader: DownloadFn = download_fn or _default_download_daily

    logger.info("Downloading daily price history: ticker=%s period=%s", safe_ticker, period)

    try:
        raw_df: pd.DataFrame = downloader(safe_ticker, period)
        return _clean_ohlcv_dataframe(raw_df, ticker=safe_ticker)
    except MarketDataError:
        # Preserve expected domain errors with clear logs.
        logger.warning(
            "Market data request failed for ticker=%s period=%s",
            safe_ticker,
            period,
            exc_info=True,
        )
        raise
    except Exception as exc:  # pragma: no cover - defensive layer
        logger.exception(
            "Unexpected market data error for ticker=%s period=%s", safe_ticker, period
        )
        raise MarketDataError(
            f"Failed to download market data for ticker '{safe_ticker}'."
        ) from exc


def get_price_history_for_tickers(
    tickers: list[str],
    period: str = "5y",
    download_fn: DownloadFn | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Fetch daily OHLCV data for multiple tickers.

    For each ticker, returns either a cleaned DataFrame or skips invalid/empty results.
    This helper is intentionally simple and can be extended with async/batching later.
    """
    results: dict[str, pd.DataFrame] = {}

    for ticker in tickers:
        try:
            safe_ticker: str = _validate_ticker(ticker)
            results[safe_ticker] = get_price_history(
                ticker=safe_ticker,
                period=period,
                download_fn=download_fn,
            )
        except MarketDataError as exc:
            logger.warning("Skipping ticker '%s': %s", ticker, exc)

    return results
