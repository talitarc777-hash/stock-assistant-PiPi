"""Batched Discord alert delivery with persistent, secret-free auditing."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Callable, Iterable, Iterator
from uuid import uuid4

from app.core.settings import get_settings
from app.services.discord_webhook import (
    DiscordWebhookDeliveryError,
    DiscordWebhookDeliveryResult,
    send_discord_webhook_message,
)
from app.services.user_profile_service import get_user_profile_store


@dataclass(frozen=True)
class DiscordAlertItem:
    """One deduplicated alert candidate ready for delivery."""

    user_id: str
    ticker: str
    rule: str
    state_key: str
    message: str


@dataclass(frozen=True)
class DiscordAlertDeliverySummary:
    alerts_requested: int
    alerts_sent: int
    batches_sent: int
    batches_failed: int


@dataclass(frozen=True)
class MessageBatch:
    content: str
    item_indexes: tuple[int, ...]


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _split_text(text: str, limit: int) -> list[str]:
    clean = str(text or "").strip()
    chunks: list[str] = []
    while len(clean) > limit:
        split_at = clean.rfind("\n", 0, limit + 1)
        if split_at < max(1, limit // 2):
            split_at = limit
        chunks.append(clean[:split_at].rstrip())
        clean = clean[split_at:].lstrip()
    if clean:
        chunks.append(clean)
    return chunks


def build_discord_alert_batches(
    items: Iterable[DiscordAlertItem],
    *,
    message_limit: int = 1900,
) -> list[MessageBatch]:
    """Batch messages while retaining item-to-batch acknowledgement mapping."""
    item_list = list(items)
    limit = max(100, min(2000, int(message_limit)))
    separator = "\n\n---\n\n"
    batches: list[MessageBatch] = []
    parts: list[str] = []
    indexes: list[int] = []

    def flush() -> None:
        if parts:
            batches.append(
                MessageBatch(
                    content=separator.join(parts),
                    item_indexes=tuple(dict.fromkeys(indexes)),
                )
            )
            parts.clear()
            indexes.clear()

    for index, item in enumerate(item_list):
        for chunk in _split_text(item.message, limit):
            candidate = separator.join([*parts, chunk])
            if parts and len(candidate) > limit:
                flush()
            parts.append(chunk)
            indexes.append(index)
            if len(separator.join(parts)) >= limit:
                flush()
    flush()
    return batches


class DiscordAlertDeliveryService:
    """Deliver batches and persist pending/sent/failed audit records."""

    def __init__(
        self,
        db_path: str | None = None,
        *,
        sender: Callable[..., DiscordWebhookDeliveryResult] = send_discord_webhook_message,
        profile_store=None,
    ) -> None:
        self.db_path = Path(db_path or get_settings().profile_db_path)
        self._sender = sender
        self._profile_store = profile_store or get_user_profile_store()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS discord_alert_delivery_audit (
                    delivery_id TEXT PRIMARY KEY,
                    batch_group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    alert_count INTEGER NOT NULL,
                    tickers_json TEXT NOT NULL,
                    rules_json TEXT NOT NULL,
                    state_keys_json TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    http_status INTEGER,
                    error_message TEXT,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    sent_at_utc TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_discord_alert_audit_status_time
                ON discord_alert_delivery_audit(status, updated_at_utc DESC)
                """
            )
            connection.commit()

    def _create_pending(
        self,
        *,
        group_id: str,
        user_id: str,
        source: str,
        items: list[DiscordAlertItem],
    ) -> str:
        delivery_id = uuid4().hex
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO discord_alert_delivery_audit(
                    delivery_id, batch_group_id, user_id, source, status,
                    alert_count, tickers_json, rules_json, state_keys_json,
                    attempt_count, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    delivery_id,
                    group_id,
                    user_id,
                    source,
                    len(items),
                    json.dumps(sorted({item.ticker for item in items})),
                    json.dumps(sorted({item.rule for item in items})),
                    json.dumps([item.state_key for item in items]),
                    now,
                    now,
                ),
            )
            connection.commit()
        return delivery_id

    def _finish(
        self,
        delivery_id: str,
        *,
        status: str,
        attempts: int,
        http_status: int | None,
        error_message: str | None,
    ) -> None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE discord_alert_delivery_audit
                SET status = ?, attempt_count = ?, http_status = ?,
                    error_message = ?, updated_at_utc = ?, sent_at_utc = ?
                WHERE delivery_id = ?
                """,
                (
                    status,
                    int(attempts),
                    http_status,
                    error_message,
                    now,
                    now if status == "sent" else None,
                    delivery_id,
                ),
            )
            connection.commit()

    def deliver(
        self,
        *,
        user_id: str,
        items: list[DiscordAlertItem],
        source: str = "scheduler",
    ) -> DiscordAlertDeliverySummary:
        """Deliver every batch and acknowledge only fully delivered alert items."""
        settings = get_settings()
        webhook_url = settings.discord_webhook_url
        if not items or not webhook_url:
            return DiscordAlertDeliverySummary(len(items), 0, 0, 0)

        batches = build_discord_alert_batches(
            items,
            message_limit=settings.discord_alert_message_limit,
        )
        group_id = uuid4().hex
        successful_batches: set[int] = set()
        failed_batches = 0

        for batch_index, batch in enumerate(batches):
            batch_items = [items[index] for index in batch.item_indexes]
            delivery_id = self._create_pending(
                group_id=group_id,
                user_id=user_id,
                source=source,
                items=batch_items,
            )
            try:
                result = self._sender(
                    webhook_url,
                    batch.content,
                    max_attempts=settings.discord_webhook_max_attempts,
                    retry_base_seconds=settings.discord_webhook_retry_base_seconds,
                )
                self._finish(
                    delivery_id,
                    status="sent",
                    attempts=result.attempts,
                    http_status=result.http_status,
                    error_message=None,
                )
                successful_batches.add(batch_index)
            except DiscordWebhookDeliveryError as exc:
                failed_batches += 1
                self._finish(
                    delivery_id,
                    status="failed",
                    attempts=exc.attempts,
                    http_status=exc.http_status,
                    error_message=str(exc)[:500],
                )
            except Exception as exc:  # pragma: no cover - defensive adapter guard
                failed_batches += 1
                self._finish(
                    delivery_id,
                    status="failed",
                    attempts=1,
                    http_status=None,
                    error_message=str(exc)[:500],
                )

        item_batches: dict[int, set[int]] = {index: set() for index in range(len(items))}
        for batch_index, batch in enumerate(batches):
            for item_index in batch.item_indexes:
                item_batches[item_index].add(batch_index)

        sent_items = 0
        for item_index, item in enumerate(items):
            required = item_batches[item_index]
            if required and required.issubset(successful_batches):
                self._profile_store.record_alert_dispatched(
                    item.user_id,
                    item.ticker,
                    item.rule,
                    item.state_key,
                )
                sent_items += 1

        return DiscordAlertDeliverySummary(
            alerts_requested=len(items),
            alerts_sent=sent_items,
            batches_sent=len(successful_batches),
            batches_failed=failed_batches,
        )

    def get_audit_health(self) -> dict:
        """Return persistent delivery evidence without message or webhook content."""
        with self._connect() as connection:
            counts = {
                row["status"]: int(row["count"])
                for row in connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM discord_alert_delivery_audit
                    GROUP BY status
                    """
                ).fetchall()
            }
            latest = connection.execute(
                """
                SELECT status, updated_at_utc, error_message,
                       attempt_count, http_status
                FROM discord_alert_delivery_audit
                ORDER BY updated_at_utc DESC, rowid DESC
                LIMIT 1
                """
            ).fetchone()
        return {
            "delivery_counts": {
                "pending": counts.get("pending", 0),
                "sent": counts.get("sent", 0),
                "failed": counts.get("failed", 0),
            },
            "last_delivery_status": latest["status"] if latest else None,
            "last_delivery_time_utc": latest["updated_at_utc"] if latest else None,
            "last_delivery_error": latest["error_message"] if latest else None,
            "last_delivery_attempt_count": int(latest["attempt_count"]) if latest else 0,
            "last_delivery_http_status": latest["http_status"] if latest else None,
        }


_SERVICE: DiscordAlertDeliveryService | None = None


def get_discord_alert_delivery_service() -> DiscordAlertDeliveryService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = DiscordAlertDeliveryService()
    return _SERVICE
