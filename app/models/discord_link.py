"""Typed models for securely linking Discord to a dashboard profile."""

from pydantic import BaseModel, Field


class DiscordLinkCodeRequest(BaseModel):
    profile_user_id: str = Field(min_length=1, max_length=120)


class DiscordLinkCodeResponse(BaseModel):
    code: str
    expires_at_utc: str
    expires_in_seconds: int


class DiscordLinkConsumeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=32)
    discord_user_id: str = Field(min_length=1, max_length=120)
    discord_display_name: str | None = Field(default=None, max_length=120)


class DiscordLinkStatusResponse(BaseModel):
    linked: bool
    profile_user_id: str
    discord_user_id: str | None = None
    discord_display_name: str | None = None
    linked_at_utc: str | None = None


class DiscordReadinessResponse(BaseModel):
    bot_commands_configured: bool
    proactive_alerts_configured: bool
    fully_configured: bool
    missing_environment_variables: list[str]


class DiscordLinkUnlinkRequest(BaseModel):
    profile_user_id: str = Field(min_length=1, max_length=120)
