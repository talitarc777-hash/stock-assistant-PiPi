"""One-time link codes connecting Discord identities to dashboard profiles."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import secrets
import sqlite3
import string
from typing import Iterator

from app.core.settings import get_settings


LINK_CODE_TTL_SECONDS = 600
LINK_CODE_ALPHABET = string.ascii_uppercase + string.digits


class DiscordLinkError(Exception):
    """Base error for Discord profile linking."""


class DiscordLinkValidationError(DiscordLinkError):
    """Raised for invalid, expired, or already-used link codes."""


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _hash_code(code: str) -> str:
    return hashlib.sha256(str(code).strip().upper().encode("utf-8")).hexdigest()


class DiscordLinkService:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = Path(db_path or get_settings().profile_db_path)
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
                CREATE TABLE IF NOT EXISTS discord_profile_links (
                    discord_user_id TEXT PRIMARY KEY,
                    profile_user_id TEXT NOT NULL UNIQUE,
                    discord_display_name TEXT,
                    linked_at_utc TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS discord_link_codes (
                    code_hash TEXT PRIMARY KEY,
                    profile_user_id TEXT NOT NULL,
                    expires_at_utc TEXT NOT NULL,
                    consumed_at_utc TEXT,
                    created_at_utc TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_discord_link_codes_profile
                ON discord_link_codes(profile_user_id, expires_at_utc)
                """
            )
            connection.commit()

    def create_link_code(self, profile_user_id: str) -> dict:
        clean_profile_id = str(profile_user_id).strip()
        if not clean_profile_id:
            raise DiscordLinkValidationError("profile_user_id is required.")
        now = _utc_now()
        expires = now + timedelta(seconds=LINK_CODE_TTL_SECONDS)
        code = "".join(secrets.choice(LINK_CODE_ALPHABET) for _ in range(8))
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM discord_link_codes
                WHERE profile_user_id = ? OR expires_at_utc <= ?
                """,
                (clean_profile_id, now.isoformat()),
            )
            connection.execute(
                """
                INSERT INTO discord_link_codes(
                    code_hash, profile_user_id, expires_at_utc, consumed_at_utc,
                    created_at_utc
                ) VALUES (?, ?, ?, NULL, ?)
                """,
                (_hash_code(code), clean_profile_id, expires.isoformat(), now.isoformat()),
            )
            connection.commit()
        return {
            "code": code,
            "expires_at_utc": expires.isoformat(),
            "expires_in_seconds": LINK_CODE_TTL_SECONDS,
        }

    def consume_link_code(
        self,
        *,
        code: str,
        discord_user_id: str,
        discord_display_name: str | None = None,
    ) -> dict:
        clean_code = str(code).strip().upper()
        clean_discord_id = str(discord_user_id).strip()
        if not clean_code or not clean_discord_id:
            raise DiscordLinkValidationError("A link code and Discord user ID are required.")
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM discord_link_codes WHERE code_hash = ?",
                (_hash_code(clean_code),),
            ).fetchone()
            if row is None:
                raise DiscordLinkValidationError("This link code is invalid.")
            if row["consumed_at_utc"]:
                raise DiscordLinkValidationError("This link code has already been used.")
            expires = datetime.fromisoformat(str(row["expires_at_utc"]))
            if expires <= now:
                raise DiscordLinkValidationError("This link code has expired. Generate a new one on the web.")

            profile_user_id = str(row["profile_user_id"])
            connection.execute(
                "DELETE FROM discord_profile_links WHERE profile_user_id = ? OR discord_user_id = ?",
                (profile_user_id, clean_discord_id),
            )
            connection.execute(
                """
                INSERT INTO discord_profile_links(
                    discord_user_id, profile_user_id, discord_display_name,
                    linked_at_utc
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    clean_discord_id,
                    profile_user_id,
                    str(discord_display_name).strip() if discord_display_name else None,
                    now.isoformat(),
                ),
            )
            connection.execute(
                "UPDATE discord_link_codes SET consumed_at_utc = ? WHERE code_hash = ?",
                (now.isoformat(), _hash_code(clean_code)),
            )
            connection.commit()
        return self.get_status_for_profile(profile_user_id)

    def resolve_profile_id(self, discord_user_id: str) -> dict:
        clean_discord_id = str(discord_user_id).strip()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM discord_profile_links WHERE discord_user_id = ?",
                (clean_discord_id,),
            ).fetchone()
        if row is None:
            return {
                "linked": False,
                "profile_user_id": clean_discord_id,
                "discord_user_id": clean_discord_id,
            }
        return {
            "linked": True,
            "profile_user_id": str(row["profile_user_id"]),
            "discord_user_id": clean_discord_id,
            "discord_display_name": row["discord_display_name"],
            "linked_at_utc": row["linked_at_utc"],
        }

    def get_status_for_profile(self, profile_user_id: str) -> dict:
        clean_profile_id = str(profile_user_id).strip()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM discord_profile_links WHERE profile_user_id = ?",
                (clean_profile_id,),
            ).fetchone()
        if row is None:
            return {"linked": False, "profile_user_id": clean_profile_id}
        return {
            "linked": True,
            "profile_user_id": clean_profile_id,
            "discord_user_id": str(row["discord_user_id"]),
            "discord_display_name": row["discord_display_name"],
            "linked_at_utc": row["linked_at_utc"],
        }

    def unlink_profile(self, profile_user_id: str) -> dict:
        clean_profile_id = str(profile_user_id).strip()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM discord_profile_links WHERE profile_user_id = ?",
                (clean_profile_id,),
            )
            connection.commit()
        return {"linked": False, "profile_user_id": clean_profile_id}


_service: DiscordLinkService | None = None


def get_discord_link_service() -> DiscordLinkService:
    global _service
    if _service is None:
        _service = DiscordLinkService()
    return _service
