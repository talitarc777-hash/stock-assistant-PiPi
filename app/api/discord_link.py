"""API endpoints for linking a dashboard profile to Discord."""

import os

from fastapi import APIRouter, HTTPException, Query

from app.models.discord_link import (
    DiscordLinkCodeRequest,
    DiscordLinkCodeResponse,
    DiscordLinkConsumeRequest,
    DiscordReadinessResponse,
    DiscordLinkStatusResponse,
    DiscordLinkUnlinkRequest,
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
