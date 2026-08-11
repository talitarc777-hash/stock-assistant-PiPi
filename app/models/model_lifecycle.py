"""Typed models for automatic model lifecycle APIs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


MODEL_REGISTRY_STATUSES = {"candidate", "production", "archived"}
MODEL_WORKFLOW_TYPES = {"daily_incremental", "weekly_full", "monthly_deep", "trigger_based"}


class ModelRegistryItemResponse(BaseModel):
    """One model registry row."""

    market: str = "US"
    ticker: str
    period: str
    target_name: str
    model_name: str
    status: str
    is_validated: bool
    stored_is_validated: bool = False
    validation_gate_version: int = 0
    validation_evidence_current: bool = False
    validation_score: float | None = None
    stale_after_days: int
    is_stale: bool
    retrain_type: str | None = None
    last_trained_at_utc: str | None = None
    last_evaluated_at_utc: str | None = None
    last_promoted_at_utc: str | None = None
    metrics_summary: dict = Field(default_factory=dict)
    notes: str | None = None
    created_at: str
    updated_at: str


class ModelLifecycleRunResponse(BaseModel):
    """One lifecycle workflow execution log row."""

    id: int
    run_type: str
    trigger_reason: str
    status: str
    started_at_utc: str
    completed_at_utc: str | None = None
    processed_tickers: int
    successful_models: int
    failed_models: int
    details: dict = Field(default_factory=dict)
    error_message: str | None = None


class ModelLifecycleStatusResponse(BaseModel):
    """Model lifecycle scheduler status snapshot."""

    running: bool
    scheduler_started: bool
    cadence_seconds: int
    last_run_time_utc: str | None = None
    next_run_time_utc: str | None = None
    last_retrain_time_utc: str | None = None
    next_retrain_time_utc: str | None = None
    last_workflow_type: str | None = None
    production_model: ModelRegistryItemResponse | None = None
    recent_metrics: list[dict] = Field(default_factory=list)
    active_triggers: list[str] = Field(default_factory=list)
    recent_runs: list[ModelLifecycleRunResponse] = Field(default_factory=list)


class ModelLifecycleRunNowRequest(BaseModel):
    """Manual workflow trigger request."""

    workflow_type: str = "daily_incremental"
    trigger_reason: str = "manual_trigger"
    tickers: list[str] | None = None
    market: Literal["US", "HK"] = "US"

