"""Typed models for immutable virtual account ledger endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


LEDGER_EVENT_TYPES = {
    "monthly_contribution",
    "manual_deposit",
    "withdrawal",
    "buy_trade",
    "sell_trade",
    "fee",
}


class AccountLedgerEventResponse(BaseModel):
    """One immutable virtual account ledger event."""

    id: int
    user_id: str
    event_type: str
    amount: float
    ticker: str | None = None
    quantity: float | None = None
    price: float | None = None
    reason: str | None = None
    source: str | None = None
    reference_month: str | None = None
    created_at: str
    metadata: dict = Field(default_factory=dict)


class AccountLedgerListResponse(BaseModel):
    """Ledger list response for one user."""

    user_id: str
    count: int
    limit: int | None = None
    offset: int | None = None
    has_more: bool | None = None
    events: list[AccountLedgerEventResponse]


class VirtualAccountHistoryEventResponse(BaseModel):
    """One immutable account-history row with derived balance context."""

    id: int
    user_id: str
    event_type: str
    created_at: str
    ticker: str | None = None
    quantity: float | None = None
    price: float | None = None
    gross_amount: float | None = None
    fee_amount: float = 0.0
    net_amount: float
    cash_change: float
    cash_balance_after: float
    reason: str | None = None
    source: str | None = None
    reference_month: str | None = None
    metadata: dict = Field(default_factory=dict)


class VirtualAccountHistoryResponse(BaseModel):
    """Full immutable account history for one profile."""

    user_id: str
    count: int
    limit: int | None = None
    offset: int | None = None
    has_more: bool | None = None
    events: list[VirtualAccountHistoryEventResponse]


class VirtualHoldingResponse(BaseModel):
    """Derived holding state from immutable buy/sell ledger events."""

    ticker: str
    quantity: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float | None = None
    latest_signal: str | None = None


class VirtualAccountHoldingsResponse(BaseModel):
    """Current holdings derived from immutable trade history."""

    user_id: str
    count: int
    holdings: list[VirtualHoldingResponse]


class VirtualAccountRecentTradeResponse(BaseModel):
    """Recent executed trade events derived from immutable ledger records."""

    id: int
    user_id: str
    created_at: str
    event_type: str
    ticker: str
    quantity: float
    remaining_quantity: float = 0.0
    price: float
    gross_amount: float
    fee_amount: float = 0.0
    net_amount: float
    cash_balance_after: float
    reason: str | None = None
    source: str | None = None
    metadata: dict = Field(default_factory=dict)


class VirtualAccountRecentTradesResponse(BaseModel):
    """Recent buy/sell activity for one profile."""

    user_id: str
    count: int
    trades: list[VirtualAccountRecentTradeResponse]


class VirtualAccountSummaryResponse(BaseModel):
    """Derived account summary rebuilt from immutable ledger history."""

    user_id: str
    as_of: str
    last_updated: str | None = None
    curve_last_point_timestamp: str | None = None
    cash: float
    holdings_value: float
    total_account_value: float
    realized_pnl: float
    unrealized_pnl: float
    net_deposits: float
    holdings: list[VirtualHoldingResponse]
    latest_prices: dict[str, float]


class VirtualAccountEquityCurvePointResponse(BaseModel):
    """One profile-level equity curve point derived from the immutable ledger."""

    timestamp: str
    cash: float
    holdings_value: float
    total_equity: float
    event_type: str | None = None
    note: str | None = None


class VirtualAccountEquityCurveResponse(BaseModel):
    """Profile-level equity curve plus latest consistency metadata."""

    user_id: str
    last_updated: str
    curve_last_point_timestamp: str | None = None
    latest_total_equity: float
    points: list[VirtualAccountEquityCurvePointResponse]
    consistent_with_latest_snapshot: bool


class VirtualAccountDepositRequest(BaseModel):
    """Request payload to create a manual deposit event."""

    user_id: str = Field(min_length=1, max_length=120)
    amount: float = Field(gt=0)
    reason: str | None = Field(default=None, max_length=200)
    source: str = Field(default="web", min_length=2, max_length=40)


class VirtualAccountWithdrawalRequest(BaseModel):
    """Request payload to create a withdrawal event."""

    user_id: str = Field(min_length=1, max_length=120)
    amount: float = Field(gt=0)
    reason: str | None = Field(default=None, max_length=200)
    source: str = Field(default="web", min_length=2, max_length=40)


class VirtualAccountResetRequest(BaseModel):
    """Request payload for destructive profile-scoped account reset."""

    user_id: str = Field(min_length=1, max_length=120)
    confirm_reset: bool = False
    reset_monthly_contributions: bool = True


class VirtualAccountResetResponse(BaseModel):
    """Response after resetting one profile's virtual account data."""

    user_id: str
    reset_completed: bool
    deleted_ledger_rows: int
    deleted_live_trade_rows: int
    deleted_live_position_rows: int
    deleted_trader_cash_rows: int
    deleted_trader_contribution_rows: int
    deleted_monthly_contribution_rows: int
    deleted_monthly_store_rows: int
    message: str


class VirtualAccountDiagnosticsResponse(BaseModel):
    """Profile-scoped persistence diagnostics snapshot."""

    user_id: str
    loaded_from_storage: bool
    ledger_row_count: int
    trade_row_count: int
    position_row_count: int
    monthly_contribution_row_count: int
    cash: float
    holdings_count: int
    total_account_value: float
    as_of: str


class MonthlyContributionCreateRequest(BaseModel):
    """Create-only monthly contribution event request."""

    user_id: str = Field(min_length=1, max_length=120)
    month: str = Field(min_length=7, max_length=7)
    amount: float = Field(gt=0)
    source: str = Field(default="web", min_length=2, max_length=40)
    reason: str | None = Field(default=None, max_length=200)

    @field_validator("month")
    @classmethod
    def validate_month(cls, value: str) -> str:
        text = str(value).strip()
        if len(text) != 7 or text[4] != "-":
            raise ValueError("month must use YYYY-MM format.")
        return text
