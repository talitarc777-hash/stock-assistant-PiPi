"""Typed API models for proactive Discord alert monitoring."""

from pydantic import BaseModel, Field


class DiscordAlertDeliveryCounts(BaseModel):
    pending: int = 0
    sent: int = 0
    failed: int = 0


class DiscordAlertHealthResponse(BaseModel):
    enabled: bool
    webhook_configured: bool
    healthy: bool
    scheduler_started: bool
    running: bool
    mode: str
    cadence_seconds: int
    last_scan_time_utc: str | None = None
    next_scan_time_utc: str | None = None
    last_users_scanned: int = 0
    last_alerts_detected: int = 0
    last_alerts_sent: int = 0
    last_batches_failed: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    delivery_counts: DiscordAlertDeliveryCounts = Field(
        default_factory=DiscordAlertDeliveryCounts
    )
    last_delivery_status: str | None = None
    last_delivery_time_utc: str | None = None
    last_delivery_error: str | None = None
    last_delivery_attempt_count: int = 0
    last_delivery_http_status: int | None = None
