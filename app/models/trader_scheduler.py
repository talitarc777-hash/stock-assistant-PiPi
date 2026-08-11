"""Typed models for trader scheduler status endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TraderSchedulerRunLogResponse(BaseModel):
    """One recent scheduler/manual run record."""

    timestamp: str
    timestamp_utc: str
    source: str
    mode: str
    users_processed: int
    tickers_processed: int
    tickers_failed: int
    fallback_used: int
    decisions_executed: int
    status: str = "success"
    errors: int = 0
    skipped: bool
    message: str
    note: str | None = None
    error_count: int = 0
    error_messages: list[str] = Field(default_factory=list)
    markets: dict[str, dict] = Field(default_factory=dict)


class TraderSchedulerStatusResponse(BaseModel):
    """Current scheduler runtime status snapshot."""

    running: bool
    scheduler_started: bool
    mode: str
    cadence_seconds: int
    cadence_label: str
    last_run_time_utc: str | None = None
    next_run_time_utc: str | None = None
    total_runs: int
    skipped_runs_total: int
    last_users_processed: int
    last_tickers_processed: int
    last_tickers_failed: int
    last_fallback_used: int
    last_decisions_executed: int
    last_error_count: int = 0
    market_states: dict[str, dict] = Field(default_factory=dict)
    recent_runs: list[TraderSchedulerRunLogResponse]


class TraderSchedulerHealthResponse(BaseModel):
    """Simple health response for scheduler runtime checks."""

    healthy: bool
    scheduler_started: bool
    running: bool
    mode: str
    last_run_time_utc: str | None = None
    next_run_time_utc: str | None = None
    consecutive_failures: int
