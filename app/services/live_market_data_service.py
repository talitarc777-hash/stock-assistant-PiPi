"""Near-live market snapshot service.

Note:
- Data is sourced from yfinance and may be delayed.
- This is a near-real-time polling snapshot, not exchange-level streaming.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import yfinance as yf

from app.services.market_data import get_price_history

logger = logging.getLogger(__name__)


def get_live_market_snapshot(ticker: str, period: str = "3mo") -> dict[str, Any]:
    """Return latest available market snapshot for one ticker."""
    symbol = str(ticker).strip().upper()
    if not symbol:
        raise ValueError("ticker is required.")

    history = get_price_history(symbol, period=period).sort_values("date")
    latest = history.iloc[-1]
    previous = history.iloc[-2] if len(history) >= 2 else latest

    latest_close = float(latest["close"])
    previous_close = float(previous["close"])
    daily_change_pct = ((latest_close / previous_close) - 1) * 100 if previous_close else 0.0

    pe_ratio = None
    market_cap = None
    company_name = None
    sector = None
    industry = None
    business_summary = None
    try:
        info = yf.Ticker(symbol).info or {}
        pe_ratio = info.get("trailingPE")
        market_cap = info.get("marketCap")
        company_name = info.get("longName") or info.get("shortName")
        sector = info.get("sector")
        industry = info.get("industry")
        business_summary = info.get("longBusinessSummary")
    except Exception as exc:  # pragma: no cover - depends on upstream provider
        logger.info("Live valuation fetch skipped for %s: %s", symbol, exc)

    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    logger.info(
        "Live market snapshot ticker=%s latest_date=%s close=%.2f change_pct=%.3f pe=%s",
        symbol,
        latest["date"],
        latest_close,
        daily_change_pct,
        pe_ratio,
    )

    return {
        "ticker": symbol,
        "fetched_at_utc": now,
        "price_timestamp": latest["date"].strftime("%Y-%m-%d"),
        "close": latest_close,
        "open": float(latest["open"]),
        "high": float(latest["high"]),
        "low": float(latest["low"]),
        "volume": float(latest["volume"]),
        "daily_change_pct": daily_change_pct,
        "pe_ratio": float(pe_ratio) if pe_ratio is not None else None,
        "market_cap": float(market_cap) if market_cap is not None else None,
        "company_name": str(company_name) if company_name else None,
        "sector": str(sector) if sector else None,
        "industry": str(industry) if industry else None,
        "business_summary": str(business_summary) if business_summary else None,
        "data_freshness_note": (
            "Near-live polling snapshot from latest available provider data; "
            "not guaranteed tick-level real-time."
        ),
    }
