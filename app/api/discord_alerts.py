"""Read-only health endpoint for proactive Discord webhook alerts."""

from fastapi import APIRouter, HTTPException

from app.models.discord_alerts import DiscordAlertHealthResponse
from app.services.discord_alert_scheduler import get_discord_alert_scheduler_service


router = APIRouter(prefix="/discord-alerts", tags=["discord-alerts"])


@router.get("/health", response_model=DiscordAlertHealthResponse)
def get_discord_alert_health() -> DiscordAlertHealthResponse:
    """Return scheduler and persistent delivery health without exposing secrets."""
    try:
        return DiscordAlertHealthResponse(
            **get_discord_alert_scheduler_service().get_health()
        )
    except Exception as exc:  # pragma: no cover - defensive health guard
        raise HTTPException(status_code=500, detail="Discord alert health is unavailable.") from exc
