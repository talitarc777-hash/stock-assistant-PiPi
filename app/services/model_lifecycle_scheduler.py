"""Background scheduler for automatic model lifecycle workflows.

This runtime is intentionally separate from the live trading scheduler.
It handles model retrain / validation / promotion workflows only.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
import json
import logging
from threading import Event, Lock, Thread
from typing import Any
from zoneinfo import ZoneInfo

from app.services.model_lifecycle_service import (
    DEFAULT_PERIOD,
    DEFAULT_TARGET_NAME,
    ModelLifecycleError,
    OUTPERFORMANCE_TARGET_NAME,
    get_model_lifecycle_service,
)
from app.services.model_feedback_service import get_model_feedback_service

logger = logging.getLogger(__name__)

_EASTERN = ZoneInfo("America/New_York")
_DAILY_AFTER_CLOSE = time(16, 30)
_WEEKLY_AFTER_CLOSE = time(17, 0)
_MONTHLY_AFTER_CLOSE = time(17, 30)
_CADENCE_SECONDS = 15 * 60


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _safe_json_load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _is_business_day(day: date) -> bool:
    return day.weekday() < 5


def _first_business_day(year: int, month: int) -> date:
    current = date(year, month, 1)
    while not _is_business_day(current):
        current += timedelta(days=1)
    return current


def _week_key(dt_et: datetime) -> str:
    iso = dt_et.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def _combine_et(day: date, value: time) -> datetime:
    return datetime(day.year, day.month, day.day, value.hour, value.minute, tzinfo=_EASTERN)


class ModelLifecycleSchedulerBusyError(Exception):
    """Raised when a run is requested while another run is active."""


class ModelLifecycleSchedulerService:
    """In-process scheduler for model lifecycle workflows."""

    def __init__(self) -> None:
        self._stop_event = Event()
        self._run_lock = Lock()
        self._state_lock = Lock()
        self._thread: Thread | None = None
        self._running = False
        self._started = False
        self._last_run_time_utc: str | None = None
        self._next_run_time_utc: str | None = None
        self._last_error: str | None = None
        self._consecutive_failures = 0

    def start(self) -> None:
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._next_run_time_utc = (
                datetime.now(UTC) + timedelta(seconds=_CADENCE_SECONDS)
            ).replace(microsecond=0).isoformat()
            self._thread = Thread(
                target=self._run_loop,
                name="stock-assistant-model-lifecycle-scheduler",
                daemon=True,
            )
            self._thread.start()
            self._started = True
        logger.info("Model lifecycle scheduler started cadence_seconds=%d", _CADENCE_SECONDS)

    def stop(self, timeout_seconds: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout_seconds)
        with self._state_lock:
            self._running = False
            self._started = False
            self._thread = None
            self._next_run_time_utc = None
        logger.info("Model lifecycle scheduler stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_cycle(source="scheduler", raise_if_busy=False)
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.exception("Model lifecycle scheduler loop error=%s", exc)
            with self._state_lock:
                self._next_run_time_utc = (
                    datetime.now(UTC) + timedelta(seconds=_CADENCE_SECONDS)
                ).replace(microsecond=0).isoformat()
            if self._stop_event.wait(_CADENCE_SECONDS):
                break

    def _due_workflow(self, now_et: datetime) -> tuple[str, str] | None:
        lifecycle = get_model_lifecycle_service()
        day_key = now_et.date().isoformat()
        week_key = _week_key(now_et)
        month_key = now_et.strftime("%Y-%m")
        first_business = _first_business_day(now_et.year, now_et.month)

        if (
            now_et.weekday() == 4
            and now_et.time() >= _WEEKLY_AFTER_CLOSE
            and lifecycle.get_state("weekly_done_key") != week_key
        ):
            return ("weekly_full", f"scheduled_weekly_close:{week_key}")

        if (
            now_et.date() == first_business
            and now_et.time() >= _MONTHLY_AFTER_CLOSE
            and lifecycle.get_state("monthly_done_key") != month_key
        ):
            return ("monthly_deep", f"scheduled_monthly_recalibration:{month_key}")

        if (
            _is_business_day(now_et.date())
            and now_et.time() >= _DAILY_AFTER_CLOSE
            and lifecycle.get_state("daily_done_key") != day_key
        ):
            return ("daily_incremental", f"scheduled_daily_close:{day_key}")

        return None

    def _mark_workflow_done(self, workflow_type: str, now_et: datetime) -> None:
        lifecycle = get_model_lifecycle_service()
        if workflow_type == "daily_incremental":
            lifecycle.set_state("daily_done_key", now_et.date().isoformat())
        elif workflow_type == "weekly_full":
            lifecycle.set_state("weekly_done_key", _week_key(now_et))
        elif workflow_type == "monthly_deep":
            lifecycle.set_state("monthly_done_key", now_et.strftime("%Y-%m"))

    def _next_retrain_time_utc(self) -> str:
        now_et = datetime.now(_EASTERN)
        candidates: list[datetime] = []

        # Next business-day daily run.
        d = now_et.date()
        while True:
            daily_dt = _combine_et(d, _DAILY_AFTER_CLOSE)
            if _is_business_day(d) and daily_dt > now_et:
                candidates.append(daily_dt)
                break
            d += timedelta(days=1)

        # Next Friday weekly run.
        wf = now_et.date()
        while wf.weekday() != 4:
            wf += timedelta(days=1)
        weekly_dt = _combine_et(wf, _WEEKLY_AFTER_CLOSE)
        if weekly_dt <= now_et:
            wf += timedelta(days=7)
            weekly_dt = _combine_et(wf, _WEEKLY_AFTER_CLOSE)
        candidates.append(weekly_dt)

        # Next first-business-day monthly run.
        my, mm = now_et.year, now_et.month
        monthly_day = _first_business_day(my, mm)
        monthly_dt = _combine_et(monthly_day, _MONTHLY_AFTER_CLOSE)
        if monthly_dt <= now_et:
            if mm == 12:
                my += 1
                mm = 1
            else:
                mm += 1
            monthly_day = _first_business_day(my, mm)
            monthly_dt = _combine_et(monthly_day, _MONTHLY_AFTER_CLOSE)
        candidates.append(monthly_dt)

        next_et = min(candidates)
        return _to_utc(next_et).replace(microsecond=0).isoformat()

    def _should_run_trigger_scan(self, now_utc: datetime) -> bool:
        lifecycle = get_model_lifecycle_service()
        last_scan = lifecycle.get_state("last_trigger_scan_utc")
        if not last_scan:
            return True
        parsed = datetime.fromisoformat(last_scan)
        parsed = _to_utc(parsed)
        return (now_utc - parsed).total_seconds() >= 2 * 3600

    def _should_fire_trigger_workflow(self, now_utc: datetime) -> bool:
        lifecycle = get_model_lifecycle_service()
        last = lifecycle.get_state("last_trigger_workflow_utc")
        if not last:
            return True
        parsed = _to_utc(datetime.fromisoformat(last))
        return (now_utc - parsed).total_seconds() >= 8 * 3600

    def _collect_benchmark_shadows(
        self,
        *,
        lifecycle,
        now_et: datetime,
        force: bool = False,
    ) -> dict[str, Any]:
        """Collect each eligible shadow candidate once after a market close."""
        day_key = now_et.date().isoformat()
        if not force and (
            not _is_business_day(now_et.date())
            or now_et.time() < _DAILY_AFTER_CLOSE
            or lifecycle.get_state("shadow_collection_done_key") == day_key
        ):
            return {"attempted": 0, "recorded": 0, "errors": [], "skipped": True}

        # Lazy import avoids coupling model-training scheduler startup to the
        # live account/trader runtime. This collector never mutates an account.
        from app.services.live_virtual_trader import (  # pylint: disable=import-outside-toplevel
            collect_benchmark_shadow_observation,
        )

        rows = lifecycle.list_registry(
            target_name=OUTPERFORMANCE_TARGET_NAME,
            limit=200,
        )
        candidates: list[str] = []
        seen: set[str] = set()
        for row in rows:
            ticker = str(row.get("ticker") or "").strip().upper()
            if (
                not ticker
                or ticker == "GLOBAL"
                or ticker in seen
                or row.get("status") not in {"candidate", "production"}
                or not bool(row.get("is_validated"))
            ):
                continue
            seen.add(ticker)
            candidates.append(ticker)

        recorded = 0
        errors: list[str] = []
        results: list[dict[str, Any]] = []
        for ticker in candidates:
            try:
                result = collect_benchmark_shadow_observation(
                    ticker=ticker,
                    benchmark="VOO",
                )
                results.append(result)
                recorded += int(bool(result.get("recorded")))
            except Exception as exc:  # pragma: no cover - provider guard
                errors.append(f"{ticker}:{exc}")
                logger.warning(
                    "Scheduled benchmark shadow collection failed ticker=%s error=%s",
                    ticker,
                    exc,
                )
        if not errors:
            lifecycle.set_state("shadow_collection_done_key", day_key)
        return {
            "attempted": len(candidates),
            "recorded": recorded,
            "errors": errors[:20],
            "results": results,
            "skipped": False,
        }

    def run_cycle(
        self,
        *,
        source: str = "scheduler",
        raise_if_busy: bool = True,
    ) -> dict[str, Any]:
        acquired = self._run_lock.acquire(blocking=False)
        if not acquired:
            if raise_if_busy:
                raise ModelLifecycleSchedulerBusyError(
                    "Model lifecycle scheduler is already running. Try again shortly."
                )
            return self.get_status(log_limit=6)

        lifecycle = get_model_lifecycle_service()
        with self._state_lock:
            self._running = True

        try:
            lifecycle.sync_registry_from_saved_artifacts(limit=400)
            now_utc = datetime.now(UTC)
            now_et = now_utc.astimezone(_EASTERN)
            shadow_collection = self._collect_benchmark_shadows(
                lifecycle=lifecycle,
                now_et=now_et,
            )
            feedback_result = (
                get_model_feedback_service().evaluate_pending(limit=300)
            )
            if (
                int(feedback_result.get("evaluated") or 0) > 0
                or int(feedback_result.get("shadow_evaluated") or 0) > 0
            ):
                refresh_result = lifecycle.refresh_feedback_scores(limit=400)
                logger.info(
                    "Model feedback settled evaluated=%d pending=%d "
                    "registry_updated=%d promoted=%d",
                    int(feedback_result.get("evaluated") or 0),
                    int(feedback_result.get("pending") or 0),
                    int(refresh_result.get("updated") or 0),
                    int(refresh_result.get("promoted") or 0),
                )
            if int(shadow_collection.get("attempted") or 0) > 0:
                logger.info(
                    "Benchmark shadow collection attempted=%d recorded=%d errors=%d",
                    int(shadow_collection.get("attempted") or 0),
                    int(shadow_collection.get("recorded") or 0),
                    len(shadow_collection.get("errors") or []),
                )
            due = self._due_workflow(now_et)

            if due:
                workflow_type, reason = due
                lifecycle.run_training_workflow(
                    workflow_type=workflow_type,
                    trigger_reason=f"{source}:{reason}",
                )
                self._mark_workflow_done(workflow_type, now_et)
            else:
                if self._should_run_trigger_scan(now_utc):
                    active_triggers = lifecycle.detect_retrain_triggers(max_models=6)
                    lifecycle.set_state("last_trigger_scan_utc", _utc_now_iso())
                    if active_triggers and self._should_fire_trigger_workflow(now_utc):
                        affected_tickers = list(
                            dict.fromkeys(
                                parts[1].strip().upper()
                                for trigger in active_triggers
                                if len(parts := str(trigger).split(":")) >= 2 and parts[1].strip()
                            )
                        )
                        lifecycle.run_training_workflow(
                            workflow_type="trigger_based",
                            trigger_reason=f"{source}:trigger_based:{';'.join(active_triggers[:3])}",
                            tickers=affected_tickers or None,
                        )
                        lifecycle.set_state("last_trigger_workflow_utc", _utc_now_iso())

            with self._state_lock:
                self._last_run_time_utc = _utc_now_iso()
                self._last_error = None
                self._consecutive_failures = 0
            return self.get_status(log_limit=6)
        except Exception as exc:  # pragma: no cover - runtime defensive guard
            with self._state_lock:
                self._last_error = str(exc)
                self._consecutive_failures += 1
            logger.exception("Model lifecycle run failed error=%s", exc)
            raise
        finally:
            with self._state_lock:
                self._running = False
            self._run_lock.release()

    def run_now(
        self,
        *,
        workflow_type: str,
        trigger_reason: str = "manual_trigger",
        tickers: list[str] | None = None,
    ) -> dict[str, Any]:
        acquired = self._run_lock.acquire(blocking=False)
        if not acquired:
            raise ModelLifecycleSchedulerBusyError(
                "Model lifecycle scheduler is already running. Try again shortly."
            )
        lifecycle = get_model_lifecycle_service()
        with self._state_lock:
            self._running = True
        try:
            lifecycle.sync_registry_from_saved_artifacts(limit=400)
            self._collect_benchmark_shadows(
                lifecycle=lifecycle,
                now_et=datetime.now(UTC).astimezone(_EASTERN),
                force=True,
            )
            get_model_feedback_service().evaluate_pending(limit=300)
            lifecycle.refresh_feedback_scores(limit=400)
            lifecycle.run_training_workflow(
                workflow_type=workflow_type,
                trigger_reason=f"manual:{trigger_reason}",
                tickers=tickers,
            )
            with self._state_lock:
                self._last_run_time_utc = _utc_now_iso()
                self._last_error = None
                self._consecutive_failures = 0
            return self.get_status(log_limit=8)
        except ModelLifecycleError:
            raise
        except Exception as exc:  # pragma: no cover - defensive guard
            with self._state_lock:
                self._last_error = str(exc)
                self._consecutive_failures += 1
            logger.exception("Manual model lifecycle run failed error=%s", exc)
            raise
        finally:
            with self._state_lock:
                self._running = False
            self._run_lock.release()

    def get_status(
        self,
        *,
        ticker: str = "VOO",
        period: str = DEFAULT_PERIOD,
        target_name: str = DEFAULT_TARGET_NAME,
        market: str = "US",
        log_limit: int = 8,
    ) -> dict[str, Any]:
        lifecycle = get_model_lifecycle_service()
        production = lifecycle.get_production_model(
            ticker=ticker,
            period=period,
            target_name=target_name,
            market=market,
        )
        active_triggers = _safe_json_load(
            lifecycle.get_state("last_active_triggers_json"),
            [],
        )
        recent_runs = lifecycle.list_recent_runs(limit=max(1, int(log_limit)))
        recent_metrics = lifecycle.get_recent_metrics(
            ticker=ticker,
            period=period,
            target_name=target_name,
            market=market,
            limit=5,
        )
        with self._state_lock:
            return {
                "running": bool(self._running),
                "scheduler_started": bool(self._started),
                "cadence_seconds": _CADENCE_SECONDS,
                "last_run_time_utc": self._last_run_time_utc,
                "next_run_time_utc": self._next_run_time_utc,
                "last_retrain_time_utc": lifecycle.get_state("last_retrain_time_utc"),
                "next_retrain_time_utc": self._next_retrain_time_utc(),
                "last_workflow_type": lifecycle.get_state("last_workflow_type"),
                "production_model": production,
                "recent_metrics": recent_metrics,
                "active_triggers": active_triggers if isinstance(active_triggers, list) else [],
                "recent_runs": recent_runs,
            }

    def get_health(self) -> dict[str, Any]:
        with self._state_lock:
            healthy = bool(self._started) and self._consecutive_failures < 4
            return {
                "healthy": healthy,
                "scheduler_started": bool(self._started),
                "running": bool(self._running),
                "last_run_time_utc": self._last_run_time_utc,
                "next_run_time_utc": self._next_run_time_utc,
                "consecutive_failures": int(self._consecutive_failures),
                "last_error": self._last_error,
            }


_SCHEDULER = ModelLifecycleSchedulerService()


def get_model_lifecycle_scheduler_service() -> ModelLifecycleSchedulerService:
    """Return the shared lifecycle scheduler singleton."""
    return _SCHEDULER
