"""Tests for Discord webhook retry and rate-limit behavior."""

from __future__ import annotations

from email.message import Message
from io import BytesIO
import unittest
from unittest.mock import MagicMock
from urllib.error import HTTPError, URLError

from app.services.discord_webhook import (
    DiscordWebhookDeliveryError,
    send_discord_webhook_message,
)


class _Response:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _http_error(status: int, retry_after: str | None = None) -> HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError(
        "https://discord.invalid/webhook",
        status,
        "failure",
        headers,
        BytesIO(b"{}"),
    )


class DiscordWebhookTests(unittest.TestCase):
    def test_retries_rate_limit_using_retry_after(self) -> None:
        opener = MagicMock(side_effect=[_http_error(429, "0.25"), _Response()])
        sleeps: list[float] = []

        result = send_discord_webhook_message(
            "https://discord.invalid/webhook",
            "hello",
            opener=opener,
            sleep_fn=sleeps.append,
        )

        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.http_status, 204)
        self.assertEqual(sleeps, [0.25])

    def test_retries_server_and_network_errors(self) -> None:
        opener = MagicMock(
            side_effect=[_http_error(503), URLError("temporary"), _Response()]
        )
        sleeps: list[float] = []

        result = send_discord_webhook_message(
            "https://discord.invalid/webhook",
            "hello",
            max_attempts=3,
            retry_base_seconds=0.1,
            opener=opener,
            sleep_fn=sleeps.append,
        )

        self.assertEqual(result.attempts, 3)
        self.assertEqual(sleeps, [0.1, 0.2])

    def test_does_not_retry_permanent_client_error(self) -> None:
        opener = MagicMock(side_effect=_http_error(400))
        with self.assertRaises(DiscordWebhookDeliveryError) as context:
            send_discord_webhook_message(
                "https://discord.invalid/webhook",
                "hello",
                opener=opener,
                sleep_fn=lambda _seconds: None,
            )
        self.assertEqual(context.exception.attempts, 1)
        self.assertEqual(context.exception.http_status, 400)
        self.assertEqual(opener.call_count, 1)


if __name__ == "__main__":
    unittest.main()
