"""API endpoints for linking a dashboard profile to Discord."""

from datetime import UTC, datetime
import os
from threading import Lock
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from app.models.discord_link import (
    DiscordLinkCodeRequest,
    DiscordLinkCodeResponse,
    DiscordLinkConsumeRequest,
    DiscordReadinessResponse,
    DiscordLinkStatusResponse,
    DiscordLinkTestMessageRequest,
    DiscordLinkTestMessageResponse,
    DiscordLinkUnlinkRequest,
)
from app.core.settings import get_settings
from app.services.discord_alert_delivery import (
    DiscordAlertItem,
    get_discord_alert_delivery_service,
)
from app.services.discord_link_service import (
    DiscordLinkValidationError,
    get_discord_link_service,
)
from bot.command_catalog import (
    DISCORD_BOT_BUILD_ID,
    SUPPORTED_PREFIX_COMMANDS,
    prefixed_commands,
)


router = APIRouter(prefix="/discord-link", tags=["discord-link"])

_TEST_MESSAGE_COOLDOWN_SECONDS = 30
_test_message_attempts: dict[str, float] = {}
_test_message_lock = Lock()


def _reserve_test_message(profile_user_id: str) -> None:
    """Limit the public settings action so it cannot be used to spam a webhook."""
    now = time.monotonic()
    with _test_message_lock:
        last_attempt = _test_message_attempts.get(profile_user_id)
        if last_attempt is not None:
            retry_after = _TEST_MESSAGE_COOLDOWN_SECONDS - (now - last_attempt)
            if retry_after > 0:
                raise HTTPException(
                    status_code=429,
                    detail=f"Please wait {max(1, int(retry_after) + 1)} seconds before sending another Discord test.",
                )
        _test_message_attempts[profile_user_id] = now


@router.get("/readiness", response_model=DiscordReadinessResponse)
def get_discord_readiness() -> dict:
    """Report Discord deployment readiness without returning either secret."""
    bot_configured = bool((os.getenv("DISCORD_BOT_TOKEN") or "").strip())
    webhook_configured = bool((os.getenv("DISCORD_WEBHOOK_URL") or "").strip())
    missing = []
    if not bot_configured:
        missing.append("DISCORD_BOT_TOKEN")
    if not webhook_configured:
        missing.append("DISCORD_WEBHOOK_URL")
    return {
        "bot_commands_configured": bot_configured,
        "proactive_alerts_configured": webhook_configured,
        "fully_configured": bot_configured and webhook_configured,
        "missing_environment_variables": missing,
        "bot_build_id": DISCORD_BOT_BUILD_ID,
        "link_command_supported_by_build": "link" in SUPPORTED_PREFIX_COMMANDS,
        "supported_commands": prefixed_commands("!"),
    }


@router.post("/code", response_model=DiscordLinkCodeResponse)
def create_discord_link_code(request: DiscordLinkCodeRequest) -> dict:
    return get_discord_link_service().create_link_code(request.profile_user_id)


@router.post("/consume", response_model=DiscordLinkStatusResponse)
def consume_discord_link_code(request: DiscordLinkConsumeRequest) -> dict:
    try:
        return get_discord_link_service().consume_link_code(
            code=request.code,
            discord_user_id=request.discord_user_id,
            discord_display_name=request.discord_display_name,
        )
    except DiscordLinkValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/resolve", response_model=DiscordLinkStatusResponse)
def resolve_discord_profile(
    discord_user_id: str = Query(..., min_length=1, max_length=120),
) -> dict:
    return get_discord_link_service().resolve_profile_id(discord_user_id)


@router.get("/status", response_model=DiscordLinkStatusResponse)
def get_discord_link_status(
    profile_user_id: str = Query(..., min_length=1, max_length=120),
) -> dict:
    return get_discord_link_service().get_status_for_profile(profile_user_id)


@router.post("/unlink", response_model=DiscordLinkStatusResponse)
def unlink_discord_profile(request: DiscordLinkUnlinkRequest) -> dict:
    return get_discord_link_service().unlink_profile(request.profile_user_id)


@router.post("/test-message", response_model=DiscordLinkTestMessageResponse)
def send_discord_link_test_message(request: DiscordLinkTestMessageRequest) -> dict:
    """Send a harmless test through the same webhook delivery path as alerts."""
    profile_user_id = request.profile_user_id.strip()
    link_status = get_discord_link_service().get_status_for_profile(profile_user_id)
    if not link_status.get("linked"):
        raise HTTPException(
            status_code=409,
            detail="Connect this web profile to Discord before sending a test message.",
        )
    if not get_settings().discord_webhook_url:
        raise HTTPException(
            status_code=503,
            detail="Automatic Discord alerts are not configured on the server.",
        )

    _reserve_test_message(profile_user_id)
    summary = get_discord_alert_delivery_service().deliver(
        user_id=profile_user_id,
        source="settings_test",
        items=[
            DiscordAlertItem(
                user_id=profile_user_id,
                ticker="SYSTEM",
                rule="discord_connection_test",
                state_key=uuid4().hex,
                message=(
                    "✅ Stock Assistant Discord connection test succeeded.\n"
                    "This channel can receive alerts from the web app. / "
                    "此頻道可接收網頁應用程式的提示。"
                ),
            )
        ],
    )
    if summary.alerts_sent != 1:
        raise HTTPException(
            status_code=502,
            detail=(
                "Discord did not accept the test message. Check the Discord alert health "
                "and server logs, then try again."
            ),
        )

    return {
        "sent": True,
        "message": "Test message sent to the configured Discord alert channel.",
        "delivered_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }
