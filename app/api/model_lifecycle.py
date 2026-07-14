"""Model lifecycle status and control endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.api_utils import PERIOD_PATTERN, TICKER_PATTERN
from app.models.model_lifecycle import (
    ModelLifecycleRunNowRequest,
    ModelLifecycleRunResponse,
    ModelLifecycleStatusResponse,
    ModelRegistryItemResponse,
)
from app.services.model_lifecycle_scheduler import (
    ModelLifecycleSchedulerBusyError,
    get_model_lifecycle_scheduler_service,
)
from app.services.model_lifecycle_service import (
    DEFAULT_TARGET_NAME,
    ModelLifecycleError,
    OUTPERFORMANCE_TARGET_NAME,
    get_model_lifecycle_service,
)
from app.services.model_feedback_service import get_model_feedback_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["model-lifecycle"])


class ModelLifecycleHealthResponse(BaseModel):
    """Simple lifecycle scheduler health response."""

    healthy: bool
    scheduler_started: bool
    running: bool
    last_run_time_utc: str | None = None
    next_run_time_utc: str | None = None
    consecutive_failures: int
    last_error: str | None = None


@router.get("/model-lifecycle/feedback")
def get_model_feedback(
    ticker: str | None = Query(
        default=None,
        min_length=1,
        max_length=15,
        pattern=TICKER_PATTERN,
    ),
    model_period: str | None = Query(default=None, pattern=PERIOD_PATTERN),
    model_name: str | None = Query(default=None, min_length=1, max_length=50),
    status: str | None = Query(default=None, pattern="^(pending|evaluated)$"),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    """Return auditable model predictions, outcomes, and summary scores."""
    service = get_model_feedback_service()
    rows = service.list_feedback(
        ticker=ticker,
        status=status,
        limit=limit,
    )
    summary = None
    if ticker and model_period and model_name:
        summary = service.get_model_summary(
            ticker=ticker,
            model_period=model_period,
            model_name=model_name,
        )
    return {
        "count": len(rows),
        "summary": summary,
        "feedback": rows,
    }


@router.post("/model-lifecycle/feedback/evaluate")
def evaluate_model_feedback() -> dict:
    """Manually settle feedback whose future five-day outcome is available."""
    service = get_model_feedback_service()
    result = service.evaluate_pending(limit=500)
    refresh = get_model_lifecycle_service().refresh_feedback_scores(
        limit=500
    )
    return {**result, "registry_refresh": refresh}


@router.get("/model-lifecycle/benchmark-shadow-feedback")
def get_benchmark_shadow_feedback(
    ticker: str = Query(
        "SPY",
        min_length=1,
        max_length=15,
        pattern=TICKER_PATTERN,
    ),
    model_period: str = Query("10y", pattern=PERIOD_PATTERN),
    model_name: str = Query("random_forest", min_length=1, max_length=50),
    status: str | None = Query(default=None, pattern="^(pending|evaluated)$"),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    """Return auditable, genuinely forward shadow-model outcomes."""
    service = get_model_feedback_service()
    rows = service.list_benchmark_shadow_feedback(
        ticker=ticker,
        model_period=model_period,
        model_name=model_name,
        status=status,
        limit=limit,
    )
    lifecycle = get_model_lifecycle_service()
    registry_rows = lifecycle.list_registry(
        ticker=ticker,
        period=model_period,
        target_name=OUTPERFORMANCE_TARGET_NAME,
        limit=100,
    )
    registry_row = next(
        (
            row
            for row in registry_rows
            if str(row.get("model_name", "")).lower() == model_name.strip().lower()
        ),
        None,
    )
    historical_evidence = None
    if registry_row:
        metrics = dict(registry_row.get("metrics_summary") or {})
        historical_evidence = {
            "ticker": registry_row.get("ticker"),
            "period": registry_row.get("period"),
            "model_name": registry_row.get("model_name"),
            "status": registry_row.get("status"),
            "is_validated": bool(registry_row.get("is_validated")),
            "is_stale": bool(registry_row.get("is_stale")),
            "validation_score": registry_row.get("validation_score"),
            "validation_gate_version": metrics.get("validation_gate_version"),
            "quality_gate": metrics.get("walk_forward_quality_gate") or {},
            "economics_gate": metrics.get("outperformance_economics_gate") or {},
        }
    return {
        "count": len(rows),
        "summary": lifecycle.get_benchmark_forward_promotion_gate(
            ticker=ticker,
            period=model_period,
            model_name=model_name,
        ),
        "historical_evidence": historical_evidence,
        "feedback": rows,
    }


@router.get("/model-lifecycle/status", response_model=ModelLifecycleStatusResponse)
def get_model_lifecycle_status(
    ticker: str = Query("VOO", min_length=1, max_length=15, pattern=TICKER_PATTERN),
    period: str = Query("5y", pattern=PERIOD_PATTERN),
    target_name: str = Query(DEFAULT_TARGET_NAME, min_length=1, max_length=50),
    log_limit: int = Query(8, ge=1, le=40),
) -> ModelLifecycleStatusResponse:
    """Return scheduler status, current production model, and recent lifecycle metrics."""
    try:
        payload = get_model_lifecycle_scheduler_service().get_status(
            ticker=ticker,
            period=period,
            target_name=target_name,
            log_limit=log_limit,
        )
        production_payload = payload.get("production_model")
        return ModelLifecycleStatusResponse(
            running=bool(payload.get("running")),
            scheduler_started=bool(payload.get("scheduler_started")),
            cadence_seconds=int(payload.get("cadence_seconds", 0)),
            last_run_time_utc=payload.get("last_run_time_utc"),
            next_run_time_utc=payload.get("next_run_time_utc"),
            last_retrain_time_utc=payload.get("last_retrain_time_utc"),
            next_retrain_time_utc=payload.get("next_retrain_time_utc"),
            last_workflow_type=payload.get("last_workflow_type"),
            production_model=(
                ModelRegistryItemResponse(**production_payload) if production_payload else None
            ),
            recent_metrics=list(payload.get("recent_metrics", [])),
            active_triggers=list(payload.get("active_triggers", [])),
            recent_runs=[ModelLifecycleRunResponse(**item) for item in payload.get("recent_runs", [])],
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected model lifecycle status error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.get("/model-lifecycle/registry", response_model=list[ModelRegistryItemResponse])
def list_model_registry(
    ticker: str | None = Query(default=None, min_length=1, max_length=15, pattern=TICKER_PATTERN),
    period: str | None = Query(default=None, pattern=PERIOD_PATTERN),
    target_name: str | None = Query(default=None, min_length=1, max_length=50),
    limit: int = Query(200, ge=1, le=1000),
) -> list[ModelRegistryItemResponse]:
    """Return model registry entries and statuses."""
    try:
        rows = get_model_lifecycle_service().list_registry(
            ticker=ticker,
            period=period,
            target_name=target_name,
            limit=limit,
        )
        return [ModelRegistryItemResponse(**row) for row in rows]
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected model lifecycle registry error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.get("/model-lifecycle/runs", response_model=list[ModelLifecycleRunResponse])
def list_model_lifecycle_runs(
    limit: int = Query(20, ge=1, le=200),
) -> list[ModelLifecycleRunResponse]:
    """Return recent scheduled/manual lifecycle workflow runs."""
    try:
        rows = get_model_lifecycle_service().list_recent_runs(limit=limit)
        return [ModelLifecycleRunResponse(**row) for row in rows]
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected model lifecycle runs error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.post("/model-lifecycle/run-now", response_model=ModelLifecycleStatusResponse)
def run_model_lifecycle_now(
    request: ModelLifecycleRunNowRequest,
) -> ModelLifecycleStatusResponse:
    """Trigger one immediate model lifecycle workflow run."""
    scheduler = get_model_lifecycle_scheduler_service()
    try:
        payload = scheduler.run_now(
            workflow_type=request.workflow_type,
            trigger_reason=request.trigger_reason,
            tickers=request.tickers,
        )
        production_payload = payload.get("production_model")
        return ModelLifecycleStatusResponse(
            running=bool(payload.get("running")),
            scheduler_started=bool(payload.get("scheduler_started")),
            cadence_seconds=int(payload.get("cadence_seconds", 0)),
            last_run_time_utc=payload.get("last_run_time_utc"),
            next_run_time_utc=payload.get("next_run_time_utc"),
            last_retrain_time_utc=payload.get("last_retrain_time_utc"),
            next_retrain_time_utc=payload.get("next_retrain_time_utc"),
            last_workflow_type=payload.get("last_workflow_type"),
            production_model=(
                ModelRegistryItemResponse(**production_payload) if production_payload else None
            ),
            recent_metrics=list(payload.get("recent_metrics", [])),
            active_triggers=list(payload.get("active_triggers", [])),
            recent_runs=[ModelLifecycleRunResponse(**item) for item in payload.get("recent_runs", [])],
        )
    except ModelLifecycleSchedulerBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ModelLifecycleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected model lifecycle manual run error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc


@router.get("/model-lifecycle/health", response_model=ModelLifecycleHealthResponse)
def get_model_lifecycle_health() -> ModelLifecycleHealthResponse:
    """Simple health endpoint for model lifecycle scheduler monitoring."""
    try:
        payload = get_model_lifecycle_scheduler_service().get_health()
        return ModelLifecycleHealthResponse(**payload)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected model lifecycle health error")
        raise HTTPException(status_code=500, detail="Unexpected server error.") from exc

