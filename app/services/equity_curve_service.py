"""Shared helpers for profile-level equity curve generation.

The latest curve point is intentionally built from the same account snapshot
logic used by the summary endpoints so current UI cards and charts stay aligned.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.account_ledger_service import get_account_ledger_service

logger = logging.getLogger(__name__)


def build_live_equity_curve(
    user_id: str,
    *,
    latest_prices: dict[str, float] | None = None,
    limit: int = 200,
    market: str = "US",
) -> dict[str, Any]:
    """Build a beginner-friendly profile equity curve from immutable ledger events."""
    ledger = get_account_ledger_service()
    events = ledger.list_events_chronological(user_id=user_id, market=market)

    cash_balance = 0.0
    positions: dict[str, float] = {}
    last_trade_prices: dict[str, float] = {}
    points: list[dict[str, Any]] = []

    def _holdings_value() -> float:
        total = 0.0
        for ticker, quantity in positions.items():
            if quantity <= 0:
                continue
            price = float(last_trade_prices.get(ticker, 0.0) or 0.0)
            total += float(quantity) * price
        return total

    for event in events:
        event_type = str(event.get("event_type", "")).lower()
        cash_balance += float(event.get("amount") or 0.0)
        ticker = str(event.get("ticker") or "").upper()
        quantity = float(event.get("quantity") or 0.0)
        price = float(event.get("price") or 0.0)

        if event_type == "buy_trade" and ticker and quantity > 0:
            positions[ticker] = float(positions.get(ticker, 0.0)) + quantity
            if price > 0:
                last_trade_prices[ticker] = price
        elif event_type == "sell_trade" and ticker and quantity > 0:
            positions[ticker] = max(0.0, float(positions.get(ticker, 0.0)) - quantity)
            if positions[ticker] <= 1e-8:
                positions.pop(ticker, None)
            if price > 0:
                last_trade_prices[ticker] = price

        holdings_value = _holdings_value()
        points.append(
            {
                "timestamp": event["created_at"],
                "cash": float(cash_balance),
                "holdings_value": float(holdings_value),
                "total_equity": float(cash_balance + holdings_value),
                "event_type": event_type or None,
                "note": event.get("reason") or None,
            }
        )

    latest_snapshot = ledger.build_account_summary(
        user_id=user_id,
        latest_prices=latest_prices,
        market=market,
    )
    latest_point = {
        "timestamp": latest_snapshot["as_of"],
        "cash": float(latest_snapshot["cash"]),
        "holdings_value": float(latest_snapshot["holdings_value"]),
        "total_equity": float(latest_snapshot["total_account_value"]),
        "event_type": "snapshot",
        "note": "latest account snapshot",
    }

    points.append(latest_point)
    if limit > 0:
        points = points[-int(limit) :]

    consistent = abs(float(latest_snapshot["total_account_value"]) - float(latest_point["total_equity"])) < 1e-9
    logger.info(
        "Live equity curve built profile_id=%s summary_cash=%.2f summary_holdings=%.2f summary_total=%.2f curve_latest=%.2f timestamp=%s",
        user_id,
        float(latest_snapshot["cash"]),
        float(latest_snapshot["holdings_value"]),
        float(latest_snapshot["total_account_value"]),
        float(latest_point["total_equity"]),
        latest_point["timestamp"],
    )

    return {
        "user_id": str(user_id).strip(),
        "market": latest_snapshot.get("market", market),
        "currency": latest_snapshot.get("currency", "USD"),
        "currency_symbol": latest_snapshot.get("currency_symbol", "$"),
        "last_updated": latest_snapshot["as_of"],
        "curve_last_point_timestamp": latest_point["timestamp"],
        "latest_total_equity": float(latest_snapshot["total_account_value"]),
        "points": points,
        "consistent_with_latest_snapshot": consistent,
    }
