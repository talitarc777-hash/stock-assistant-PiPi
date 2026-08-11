"""Typed API models for live virtual trader endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.ticker_classification import ClassifiedTickerResponse


class LiveTraderRunRequest(BaseModel):
    """Request body for running live simulated trader now."""

    user_id: str = Field(min_length=1, max_length=120)
    market: Literal["US", "HK"] = "US"
    tickers: list[str] | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=80)
    auto_run: bool = True


class LiveTraderDecisionResponse(ClassifiedTickerResponse):
    market: Literal["US", "HK"] = "US"
    currency: str = "USD"
    timestamp: str
    user_id: str
    action: str
    quantity: float
    price: float
    model_name: str
    confidence_score: float | None = None
    reason: str
    threshold_summary: str
    technical_state_summary: str
    news_sentiment_summary: str
    benchmark_strength_summary: str
    action_summary: str
    cash_after: float
    holdings_after: float
    realized_pnl: float
    unrealized_pnl: float
    metadata: dict | None = None


class LiveTraderHoldingResponse(ClassifiedTickerResponse):
    market: Literal["US", "HK"] = "US"
    currency: str = "USD"
    currency_symbol: str = "$"
    board_lot: int | None = None
    user_id: str | None = None
    quantity: float
    avg_entry_price: float
    entry_timestamp: str | None = None
    model_name: str | None = None
    updated_at: str | None = None
    current_price: float
    market_value: float
    unrealized_pnl: float


class LiveTraderContributionEventResponse(BaseModel):
    user_id: str | None = None
    month: str | None = None
    configured_amount: float | None = None
    applied_amount: float | None = None
    delta_applied_now: float | None = None
    applied_at: str | None = None
    event_type: str | None = None
    amount: float | None = None
    created_at: str | None = None


class LiveTraderAccountResponse(BaseModel):
    market: Literal["US", "HK"] = "US"
    currency: str = "USD"
    currency_symbol: str = "$"
    snapshot_timestamp: str | None = None
    curve_last_point_timestamp: str | None = None
    cash: float
    realized_pnl: float
    total_contributions_applied: float
    holdings_value: float
    total_equity: float
    unrealized_pnl: float | None = None
    net_deposits: float | None = None
    portfolio_risk_level: str = "unavailable"
    performance_vs_contributions_pct: float | None = None
    buying_paused: bool = False
    position_size_multiplier: float = 1.0


class LiveTraderEquityPointResponse(BaseModel):
    timestamp: str
    cash: float
    holdings_value: float
    total_equity: float
    event_type: str | None = None
    note: str | None = None


class LiveTraderStatusResponse(BaseModel):
    user_id: str
    market: Literal["US", "HK"] = "US"
    currency: str = "USD"
    currency_symbol: str = "$"
    model_name: str
    generated_at_utc: str
    account: LiveTraderAccountResponse
    holdings: list[LiveTraderHoldingResponse]
    latest_decisions: list[LiveTraderDecisionResponse]
    contribution_events: list[dict]
    universe_size: int = 0
    tickers_evaluated: int = 0
    tickers_failed: int = 0
    fallback_used_count: int = 0
    equity_curve: list[LiveTraderEquityPointResponse] = Field(default_factory=list)


class LiveTraderTradesResponse(BaseModel):
    user_id: str
    market: Literal["US", "HK"] = "US"
    count: int
    trades: list[LiveTraderDecisionResponse]
    contribution_application_history: list[dict]


class LiveTraderSyncResponse(BaseModel):
    """One lightweight, read-only snapshot used for web/Discord synchronization."""

    user_id: str
    market: Literal["US", "HK"] = "US"
    synced_at_utc: str
    watchlist: list[str] = Field(default_factory=list)
    using_system_default_watchlist: bool = False
    status: LiveTraderStatusResponse
    recent_trades: list[dict] = Field(default_factory=list)
    decisions: list[LiveTraderDecisionResponse] = Field(default_factory=list)
