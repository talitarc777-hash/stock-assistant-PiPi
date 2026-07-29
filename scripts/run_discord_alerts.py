"""Run Discord alert scans without executing or changing virtual trades."""

from __future__ import annotations

import argparse
import json
from uuid import uuid4

from bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from app.services.discord_alert_delivery import (  # noqa: E402
    DiscordAlertItem,
    get_discord_alert_delivery_service,
)
from app.services.discord_alert_scheduler import DiscordAlertSchedulerService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan and deliver Discord alerts without running the virtual trader."
    )
    parser.add_argument("--user-id", default="demo-user")
    parser.add_argument(
        "--test-webhook",
        action="store_true",
        help="Send one harmless delivery test instead of scanning market conditions.",
    )
    args = parser.parse_args()

    if args.test_webhook:
        state_key = uuid4().hex
        result = get_discord_alert_delivery_service().deliver(
            user_id=args.user_id,
            source="cli_test",
            items=[
                DiscordAlertItem(
                    user_id=args.user_id,
                    ticker="SYSTEM",
                    rule="discord_webhook_test",
                    state_key=state_key,
                    message="✅ Stock Assistant Discord webhook test succeeded.",
                )
            ],
        )
        print(json.dumps(result.__dict__, indent=2))
        if result.alerts_sent != 1:
            raise SystemExit(1)
        return

    service = DiscordAlertSchedulerService()
    result = service.run_cycle(
        source="cli",
        user_ids=[args.user_id],
        raise_if_busy=True,
    )
    print(json.dumps(result, indent=2))
    if result.get("last_error") or int(result.get("last_batches_failed", 0)) > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
