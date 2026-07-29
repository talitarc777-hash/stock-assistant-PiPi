"""Independent scheduler for proactive Discord webhook alerts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from threading import Event, Lock, Thread

from app.core.settings import get_settings
from app.services.discord_alert_delivery import (
    DiscordAlertItem,
    get_discord_alert_delivery_service,
)
from app.services.market_hours_service import get_market_hours_state
from app.services.real_market_discord_alerts import collect_real_market_activity_alerts
from app.services.score_discord_alerts import collect_overall_score_alerts
from app.services.user_profile_service import get_user_profile_store

logger = logging.getLogger(__name__)


class DiscordAlertSchedulerBusyError(RuntimeError):
    """Raised when a manual scan overlaps an existing alert scan."""


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class DiscordAlertSchedulerService:
    """Scan alert-enabled profiles without depending on the trading cycle."""

    def __init__(self) -> None:
        self._stop_event = Event()
        self._run_lock = Lock()
        self._state_lock = Lock()
        self._thread: Thread | None = None
        self._scheduler_started = False
        self._running = False
        self._mode = "market_closed"
        self._cadence_seconds = 3600
        self._last_scan_time_utc: str | None = None
        self._next_scan_time_utc: str | None = None
        self._last_users_scanned = 0
        self._last_alerts_detected = 0
        self._last_alerts_sent = 0
        self._last_batches_failed = 0
        self._last_error: str | None = None
        self._consecutive_failures = 0

    def start(self) -> None:
        settings = get_settings()
        if not settings.discord_alert_scheduler_enabled:
            logger.info("Discord alert scheduler disabled by configuration")
            return
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            state = get_market_hours_state()
            self._mode = state.mode
            self._cadence_seconds = state.interval_seconds
            self._stop_event.clear()
            self._thread = Thread(
                target=self._run_loop,
                name="stock-assistant-discord-alert-scheduler",
                daemon=True,
            )
            self._thread.start()
            self._scheduler_started = True
        logger.info(
            "Discord alert scheduler started mode=%s cadence_seconds=%d webhook_configured=%s",
            self._mode,
            self._cadence_seconds,
            bool(settings.discord_webhook_url),
        )

    def stop(self, timeout_seconds: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout_seconds)
        with self._state_lock:
            self._running = False
            self._scheduler_started = False
            self._thread = None
            self._next_scan_time_utc = None
        logger.info("Discord alert scheduler stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_cycle(source="scheduler", raise_if_busy=False)
            except Exception as exc:  # pragma: no cover - thread survival guard
                logger.exception("Discord alert scheduler cycle failed error=%s", exc)
            state = get_market_hours_state()
            with self._state_lock:
                self._mode = state.mode
                self._cadence_seconds = state.interval_seconds
                next_scan = _utc_now() + timedelta(seconds=self._cadence_seconds)
                self._next_scan_time_utc = next_scan.isoformat()
                cadence = self._cadence_seconds
            if self._stop_event.wait(cadence):
                break

    def run_cycle(
        self,
        *,
        source: str = "manual",
        user_ids: list[str] | None = None,
        raise_if_busy: bool = True,
    ) -> dict:
        """Collect and deliver alert-only results; never execute virtual trades."""
        if not self._run_lock.acquire(blocking=False):
            if raise_if_busy:
                raise DiscordAlertSchedulerBusyError("Discord alert scan is already running.")
            return self.get_health()

        settings = get_settings()
        with self._state_lock:
            self._running = True
        users_scanned = 0
        alerts_detected = 0
        alerts_sent = 0
        batches_failed = 0
        errors: list[str] = []

        try:
            if not settings.discord_alert_scheduler_enabled:
                errors.append("Discord alert scheduler is disabled.")
            elif not settings.discord_webhook_url:
                errors.append("DISCORD_WEBHOOK_URL is not configured.")
            else:
                summaries = get_user_profile_store().list_alert_enabled_user_summaries()
                allowed_ids = {str(value).strip() for value in (user_ids or []) if str(value).strip()}
                if allowed_ids:
                    summaries = [row for row in summaries if row.user_id in allowed_ids]
                summaries = [
                    row for row in summaries
                    if str(row.preferred_delivery_source) == "discord"
                ]

                delivery = get_discord_alert_delivery_service()
                for profile in summaries:
                    try:
                        score_alerts = collect_overall_score_alerts(
                            user_id=profile.user_id,
                            tickers=list(profile.alert_watchlist),
                        )
                        market_alerts = collect_real_market_activity_alerts(
                            user_id=profile.user_id,
                            tickers=list(profile.alert_watchlist),
                        )
                        items = [
                            DiscordAlertItem(
                                user_id=alert.user_id,
                                ticker=alert.ticker,
                                rule="score_above_threshold_discord",
                                state_key=alert.state_key,
                                message=alert.message,
                            )
                            for alert in score_alerts
                        ]
                        items.extend(
                            DiscordAlertItem(
                                user_id=alert.user_id,
                                ticker=alert.ticker,
                                rule=f"real_market_{alert.alert_type}_{alert.pressure}",
                                state_key=alert.state_key,
                                message=alert.message,
                            )
                            for alert in market_alerts
                        )
                        result = delivery.deliver(
                            user_id=profile.user_id,
                            items=items,
                            source=source,
                        )
                        users_scanned += 1
                        alerts_detected += len(items)
                        alerts_sent += result.alerts_sent
                        batches_failed += result.batches_failed
                    except Exception as exc:  # pragma: no cover - one user must not block others
                        errors.append(f"{profile.user_id}: {str(exc)}")
                        logger.exception(
                            "Discord alert user scan failed user_id=%s error=%s",
                            profile.user_id,
                            exc,
                        )

            now = _utc_now().isoformat()
            with self._state_lock:
                self._last_scan_time_utc = now
                self._last_users_scanned = users_scanned
                self._last_alerts_detected = alerts_detected
                self._last_alerts_sent = alerts_sent
                self._last_batches_failed = batches_failed
                self._last_error = "; ".join(errors)[:1000] if errors else None
                if errors or batches_failed:
                    self._consecutive_failures += 1
                else:
                    self._consecutive_failures = 0
            logger.info(
                "Discord alert scan source=%s users=%d detected=%d sent=%d failed_batches=%d errors=%d",
                source,
                users_scanned,
                alerts_detected,
                alerts_sent,
                batches_failed,
                len(errors),
            )
            return self.get_health()
        finally:
            with self._state_lock:
                self._running = False
            self._run_lock.release()

    def get_health(self) -> dict:
        settings = get_settings()
        with self._state_lock:
            snapshot = {
                "enabled": settings.discord_alert_scheduler_enabled,
                "webhook_configured": bool(settings.discord_webhook_url),
                "healthy": bool(
                    settings.discord_alert_scheduler_enabled
                    and settings.discord_webhook_url
                    and self._scheduler_started
                    and self._consecutive_failures == 0
                ),
                "scheduler_started": self._scheduler_started,
                "running": self._running,
                "mode": self._mode,
                "cadence_seconds": self._cadence_seconds,
                "last_scan_time_utc": self._last_scan_time_utc,
                "next_scan_time_utc": self._next_scan_time_utc,
                "last_users_scanned": self._last_users_scanned,
                "last_alerts_detected": self._last_alerts_detected,
                "last_alerts_sent": self._last_alerts_sent,
                "last_batches_failed": self._last_batches_failed,
                "consecutive_failures": self._consecutive_failures,
                "last_error": self._last_error,
            }
        snapshot.update(get_discord_alert_delivery_service().get_audit_health())
        return snapshot


_SERVICE = DiscordAlertSchedulerService()


def get_discord_alert_scheduler_service() -> DiscordAlertSchedulerService:
    return _SERVICE
