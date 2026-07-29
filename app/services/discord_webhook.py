"""Small shared Discord webhook delivery helper with bounded retries."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class DiscordWebhookDeliveryResult:
    """Successful webhook delivery metadata without exposing the secret URL."""

    attempts: int
    http_status: int


class DiscordWebhookDeliveryError(RuntimeError):
    """Webhook failure with retry diagnostics safe for logs and health output."""

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = int(attempts)
        self.http_status = http_status


def _retry_after_seconds(error: HTTPError) -> float | None:
    """Read Discord's Retry-After header without depending on response JSON."""
    raw_value = error.headers.get("Retry-After") if error.headers else None
    if raw_value is None:
        return None
    try:
        return max(0.0, float(raw_value))
    except (TypeError, ValueError):
        return None


def send_discord_webhook_message(
    webhook_url: str,
    message: str,
    timeout_seconds: float = 4.0,
    *,
    max_attempts: int = 3,
    retry_base_seconds: float = 0.5,
    opener: Callable = urlopen,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> DiscordWebhookDeliveryResult:
    """Send one Discord message with bounded retries for transient failures."""
    clean_url = str(webhook_url or "").strip()
    clean_message = str(message or "").strip()
    if not clean_url:
        raise DiscordWebhookDeliveryError(
            "Discord webhook URL is not configured.",
            attempts=0,
        )
    if not clean_message:
        raise DiscordWebhookDeliveryError(
            "Discord webhook message is empty.",
            attempts=0,
        )

    payload = json.dumps({"content": clean_message}, ensure_ascii=False).encode("utf-8")
    attempts = max(1, int(max_attempts))
    base_delay = max(0.0, float(retry_base_seconds))

    for attempt in range(1, attempts + 1):
        request = Request(
            clean_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "StockAssistantPiPi/1.0",
            },
            method="POST",
        )
        try:
            with opener(request, timeout=timeout_seconds) as response:
                status = int(getattr(response, "status", 204))
                if status >= 400:
                    raise DiscordWebhookDeliveryError(
                        f"Discord webhook failed with HTTP {status}.",
                        attempts=attempt,
                        http_status=status,
                    )
                return DiscordWebhookDeliveryResult(attempts=attempt, http_status=status)
        except HTTPError as exc:
            status = int(exc.code)
            retryable = status == 429 or status >= 500
            delay = _retry_after_seconds(exc)
            exc.close()
            if not retryable or attempt >= attempts:
                raise DiscordWebhookDeliveryError(
                    f"Discord webhook failed with HTTP {status}.",
                    attempts=attempt,
                    http_status=status,
                ) from exc
            if delay is None:
                delay = base_delay * (2 ** (attempt - 1))
            sleep_fn(delay)
        except DiscordWebhookDeliveryError:
            raise
        except (URLError, TimeoutError, OSError) as exc:
            if attempt >= attempts:
                raise DiscordWebhookDeliveryError(
                    "Discord webhook delivery failed after transient network errors.",
                    attempts=attempt,
                ) from exc
            sleep_fn(base_delay * (2 ** (attempt - 1)))

    raise DiscordWebhookDeliveryError(
        "Discord webhook delivery failed.",
        attempts=attempts,
    )
