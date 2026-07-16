"""Small shared Discord webhook delivery helper."""

from __future__ import annotations

import json
from urllib.request import Request, urlopen


def send_discord_webhook_message(
    webhook_url: str,
    message: str,
    timeout_seconds: float = 4.0,
) -> None:
    """Send one plain Discord webhook message and reject HTTP errors."""
    payload = json.dumps({"content": message}, ensure_ascii=False).encode("utf-8")
    request = Request(
        webhook_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "StockAssistantPiPi/1.0",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        status = getattr(response, "status", 204)
        if status >= 400:
            raise RuntimeError(f"Discord webhook failed with HTTP {status}")
