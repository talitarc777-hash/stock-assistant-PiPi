"""SQLite-backed user profile store shared by dashboard and Discord."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.settings import get_settings
from app.models.user_profile import (
    AlertEnabledUserResponse,
    UserAlertSettingsUpdateRequest,
    UserProfileResponse,
    UserProfileResetRequest,
    UserProfileSettingsUpdateRequest,
)

logger = logging.getLogger(__name__)

VALID_LANGUAGES = {"en", "zh", "bilingual"}
VALID_ACTIVITY_SOURCES = {"discord", "dashboard"}
VALID_DELIVERY_SOURCES = {"discord", "dashboard"}


class UserProfileError(Exception):
    """Base error for shared user profile operations."""


class UserProfileValidationError(UserProfileError):
    """Raised when user profile input is invalid."""


@dataclass(frozen=True)
class AlertDispatchState:
    """Stored dispatch state for duplicate-alert suppression."""

    user_id: str
    ticker: str
    rule: str
    state_key: str
    last_triggered_at: str


def _utc_now() -> str:
    """Return an ISO timestamp in UTC."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _canonicalize_ticker(value: str) -> str:
    """Normalize user-entered ticker symbols into a backend-friendly format."""
    raw = str(value).strip().upper().replace(" ", "")
    if not raw:
        return ""

    special_map = {
        "BRKB": "BRK-B",
        "BRK.B": "BRK-B",
        "BRK/B": "BRK-B",
        "BFB": "BF-B",
        "BF.B": "BF-B",
        "BF/B": "BF-B",
    }
    if raw in special_map:
        return special_map[raw]

    # Backward compatibility for previously normalized exchange tickers.
    # Example: old "0700-HK" should become "0700.HK" for Yahoo compatibility.
    if re.fullmatch(r"\d{3,6}-[A-Z]{1,4}", raw):
        symbol, suffix = raw.rsplit("-", 1)
        if suffix in {"HK", "SS", "SZ", "TW", "KS", "T"}:
            return f"{symbol}.{suffix}"

    # Keep exchange separators like ".HK" untouched because Yahoo symbols
    # rely on dot suffixes (e.g. 0700.HK). Only normalize obvious separators.
    return raw.replace("/", "-").replace("_", "-")


def _normalize_watchlist(values: list[str]) -> list[str]:
    """Normalize tickers into unique, canonical watchlist values."""
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        clean = _canonicalize_ticker(value)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)
    return normalized


def _json_dump(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


def _json_load(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return _normalize_watchlist([str(item) for item in loaded])


def _quote_sql_string(value: str) -> str:
    """Quote a string literal for simple SQLite migration defaults."""
    return "'" + value.replace("'", "''") + "'"


class UserProfileStore:
    """Small data-access layer around the local SQLite user profile store."""

    def __init__(self, db_path: str | None = None) -> None:
        settings = get_settings()
        self.db_path = Path(db_path or settings.profile_db_path)
        self.default_watchlist = _normalize_watchlist(settings.default_watchlist)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    display_name TEXT,
                    preferred_language TEXT NOT NULL,
                    compact_mode INTEGER NOT NULL,
                    selected_evaluation_model TEXT NOT NULL DEFAULT 'logistic_regression',
                    default_watchlist TEXT NOT NULL,
                    alert_enabled INTEGER NOT NULL,
                    alert_threshold_high INTEGER NOT NULL,
                    alert_threshold_low INTEGER NOT NULL,
                    alert_watchlist TEXT NOT NULL,
                    preferred_delivery_source TEXT NOT NULL,
                    last_active_source TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_dispatch_history (
                    user_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    state_key TEXT NOT NULL,
                    last_triggered_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, ticker, rule)
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(user_profiles)").fetchall()
            }
            migration_columns: dict[str, str] = {
                "display_name": "TEXT",
                "preferred_language": "TEXT NOT NULL DEFAULT 'bilingual'",
                "compact_mode": "INTEGER NOT NULL DEFAULT 0",
                "selected_evaluation_model": "TEXT NOT NULL DEFAULT 'logistic_regression'",
                "default_watchlist": f"TEXT NOT NULL DEFAULT {_quote_sql_string(_json_dump([]))}",
                "alert_enabled": "INTEGER NOT NULL DEFAULT 1",
                "alert_threshold_high": "INTEGER NOT NULL DEFAULT 80",
                "alert_threshold_low": "INTEGER NOT NULL DEFAULT 45",
                "alert_watchlist": f"TEXT NOT NULL DEFAULT {_quote_sql_string(_json_dump([]))}",
                "preferred_delivery_source": "TEXT NOT NULL DEFAULT 'discord'",
                "last_active_source": "TEXT",
                "created_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
                "updated_at": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
            }
            for column_name, column_definition in migration_columns.items():
                if column_name not in columns:
                    logger.info("Migrating user_profiles add column=%s", column_name)
                    connection.execute(
                        f"ALTER TABLE user_profiles ADD COLUMN {column_name} {column_definition}"
                    )
            connection.commit()

    def _default_profile(self, user_id: str, display_name: str | None = None) -> dict:
        now = _utc_now()
        return {
            "user_id": user_id,
            "display_name": display_name,
            "preferred_language": "bilingual",
            "compact_mode": 0,
            "selected_evaluation_model": "logistic_regression",
            "default_watchlist": _json_dump([]),
            "alert_enabled": 1,
            "alert_threshold_high": 80,
            "alert_threshold_low": 45,
            "alert_watchlist": _json_dump([]),
            "preferred_delivery_source": "discord",
            "last_active_source": None,
            "created_at": now,
            "updated_at": now,
        }

    def _row_to_profile(self, row: sqlite3.Row) -> UserProfileResponse:
        return UserProfileResponse(
            user_id=row["user_id"],
            display_name=row["display_name"],
            preferred_language=row["preferred_language"],
            compact_mode=bool(row["compact_mode"]),
            selected_evaluation_model=row["selected_evaluation_model"] or "logistic_regression",
            default_watchlist=_json_load(row["default_watchlist"]),
            alert_enabled=bool(row["alert_enabled"]),
            alert_threshold_high=int(row["alert_threshold_high"]),
            alert_threshold_low=int(row["alert_threshold_low"]),
            alert_watchlist=_json_load(row["alert_watchlist"]),
            preferred_delivery_source=row["preferred_delivery_source"],
            last_active_source=row["last_active_source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_or_create_profile(
        self,
        user_id: str,
        display_name: str | None = None,
        last_active_source: str | None = None,
    ) -> UserProfileResponse:
        clean_user_id = str(user_id).strip()
        if not clean_user_id:
            raise UserProfileValidationError("user_id is required.")

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?",
                (clean_user_id,),
            ).fetchone()

            if row is None:
                payload = self._default_profile(clean_user_id, display_name=display_name)
                if last_active_source in VALID_ACTIVITY_SOURCES:
                    payload["last_active_source"] = last_active_source
                connection.execute(
                    """
                    INSERT INTO user_profiles (
                        user_id, display_name, preferred_language, compact_mode,
                        selected_evaluation_model, default_watchlist, alert_enabled, alert_threshold_high,
                        alert_threshold_low, alert_watchlist, preferred_delivery_source,
                        last_active_source, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["user_id"],
                        payload["display_name"],
                        payload["preferred_language"],
                        payload["compact_mode"],
                        payload["selected_evaluation_model"],
                        payload["default_watchlist"],
                        payload["alert_enabled"],
                        payload["alert_threshold_high"],
                        payload["alert_threshold_low"],
                        payload["alert_watchlist"],
                        payload["preferred_delivery_source"],
                        payload["last_active_source"],
                        payload["created_at"],
                        payload["updated_at"],
                    ),
                )
                connection.commit()
                logger.info("Created user profile user_id=%s source=%s", clean_user_id, last_active_source)
                row = connection.execute(
                    "SELECT * FROM user_profiles WHERE user_id = ?",
                    (clean_user_id,),
                ).fetchone()

            profile = self._row_to_profile(row)

        if display_name or last_active_source:
            return self.touch_profile(
                user_id=clean_user_id,
                display_name=display_name,
                last_active_source=last_active_source,
            )
        return profile

    def touch_profile(
        self,
        user_id: str,
        display_name: str | None = None,
        last_active_source: str | None = None,
    ) -> UserProfileResponse:
        self.get_or_create_profile(user_id=user_id)
        now = _utc_now()
        updates: list[str] = ["updated_at = ?"]
        params: list[object] = [now]

        if display_name is not None:
            updates.append("display_name = ?")
            params.append(display_name.strip() or None)
        if last_active_source in VALID_ACTIVITY_SOURCES:
            updates.append("last_active_source = ?")
            params.append(last_active_source)

        if len(updates) == 1:
            return self.get_or_create_profile(user_id=user_id)

        params.append(user_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE user_profiles SET {', '.join(updates)} WHERE user_id = ?",
                tuple(params),
            )
            connection.commit()
        return self.get_or_create_profile(user_id=user_id)

    def update_profile_settings(
        self,
        request: UserProfileSettingsUpdateRequest,
    ) -> UserProfileResponse:
        profile = self.get_or_create_profile(
            user_id=request.user_id,
            display_name=request.display_name,
            last_active_source=request.last_active_source,
        )

        updates: list[str] = []
        params: list[object] = []

        if request.display_name is not None:
            updates.append("display_name = ?")
            params.append(request.display_name.strip() or None)

        if request.preferred_language is not None:
            if request.preferred_language not in VALID_LANGUAGES:
                raise UserProfileValidationError("preferred_language must be en, zh, or bilingual.")
            updates.append("preferred_language = ?")
            params.append(request.preferred_language)

        if request.compact_mode is not None:
            updates.append("compact_mode = ?")
            params.append(1 if request.compact_mode else 0)

        if request.selected_evaluation_model is not None:
            updates.append("selected_evaluation_model = ?")
            params.append(request.selected_evaluation_model.strip().lower())

        if request.default_watchlist is not None:
            updates.append("default_watchlist = ?")
            params.append(_json_dump(_normalize_watchlist(request.default_watchlist)))

        if request.last_active_source is not None:
            if request.last_active_source not in VALID_ACTIVITY_SOURCES:
                raise UserProfileValidationError("last_active_source must be discord or dashboard.")
            updates.append("last_active_source = ?")
            params.append(request.last_active_source)

        if not updates:
            return profile

        updates.append("updated_at = ?")
        params.append(_utc_now())
        params.append(request.user_id)

        with self._connect() as connection:
            connection.execute(
                f"UPDATE user_profiles SET {', '.join(updates)} WHERE user_id = ?",
                tuple(params),
            )
            connection.commit()

        logger.info("Updated user profile settings user_id=%s", request.user_id)
        return self.get_or_create_profile(request.user_id)

    def reset_profile(self, request: UserProfileResetRequest) -> UserProfileResponse:
        """Reset all user-configurable profile fields back to defaults."""
        profile = self.get_or_create_profile(
            user_id=request.user_id,
            display_name=request.display_name,
            last_active_source=request.last_active_source,
        )

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE user_profiles
                SET display_name = ?,
                    preferred_language = ?,
                    compact_mode = ?,
                    selected_evaluation_model = ?,
                    default_watchlist = ?,
                    alert_enabled = ?,
                    alert_threshold_high = ?,
                    alert_threshold_low = ?,
                    alert_watchlist = ?,
                    preferred_delivery_source = ?,
                    last_active_source = ?,
                    updated_at = ?
                WHERE user_id = ?
                """,
                (
                    request.display_name.strip() if request.display_name else profile.display_name,
                    "bilingual",
                    0,
                    "logistic_regression",
                    _json_dump([]),
                    1,
                    80,
                    45,
                    _json_dump([]),
                    "discord",
                    request.last_active_source,
                    _utc_now(),
                    request.user_id,
                ),
            )
            connection.execute(
                "DELETE FROM alert_dispatch_history WHERE user_id = ?",
                (request.user_id,),
            )
            connection.commit()

        logger.info("Reset user profile user_id=%s", request.user_id)
        return self.get_or_create_profile(request.user_id)

    def get_effective_watchlist(self, user_id: str) -> tuple[list[str], bool, UserProfileResponse]:
        profile = self.get_or_create_profile(user_id=user_id)
        watchlist = profile.default_watchlist or list(self.default_watchlist)
        using_system_default = not bool(profile.default_watchlist)
        return watchlist, using_system_default, profile

    def add_watchlist_ticker(
        self,
        user_id: str,
        ticker: str,
        display_name: str | None = None,
        last_active_source: str | None = None,
    ) -> UserProfileResponse:
        clean_ticker = _canonicalize_ticker(ticker)
        if not clean_ticker:
            raise UserProfileValidationError("Ticker cannot be empty.")

        watchlist, _, _ = self.get_effective_watchlist(user_id=user_id)
        updated_watchlist = _normalize_watchlist(watchlist + [clean_ticker])
        profile = self.update_profile_settings(
            UserProfileSettingsUpdateRequest(
                user_id=user_id,
                display_name=display_name,
                default_watchlist=updated_watchlist,
                last_active_source=last_active_source,
            )
        )
        logger.info("Added ticker to watchlist user_id=%s ticker=%s", user_id, clean_ticker)
        return profile

    def remove_watchlist_ticker(
        self,
        user_id: str,
        ticker: str,
        display_name: str | None = None,
        last_active_source: str | None = None,
    ) -> UserProfileResponse:
        clean_ticker = _canonicalize_ticker(ticker)
        if not clean_ticker:
            raise UserProfileValidationError("Ticker cannot be empty.")

        watchlist, _, _ = self.get_effective_watchlist(user_id=user_id)
        updated_watchlist = [item for item in watchlist if item != clean_ticker]
        if not updated_watchlist:
            raise UserProfileValidationError(
                "Watchlist cannot be empty. Add another ticker before removing the last one."
            )

        profile = self.update_profile_settings(
            UserProfileSettingsUpdateRequest(
                user_id=user_id,
                display_name=display_name,
                default_watchlist=updated_watchlist,
                last_active_source=last_active_source,
            )
        )
        logger.info("Removed ticker from watchlist user_id=%s ticker=%s", user_id, clean_ticker)
        return profile

    def update_alert_settings(
        self,
        request: UserAlertSettingsUpdateRequest,
    ) -> UserProfileResponse:
        self.get_or_create_profile(
            user_id=request.user_id,
            display_name=request.display_name,
            last_active_source=request.last_active_source,
        )

        if (
            request.alert_threshold_high is not None
            and request.alert_threshold_low is not None
            and request.alert_threshold_low >= request.alert_threshold_high
        ):
            raise UserProfileValidationError("alert_threshold_low must be lower than alert_threshold_high.")

        updates: list[str] = []
        params: list[object] = []

        if request.display_name is not None:
            updates.append("display_name = ?")
            params.append(request.display_name.strip() or None)
        if request.alert_enabled is not None:
            updates.append("alert_enabled = ?")
            params.append(1 if request.alert_enabled else 0)
        if request.alert_threshold_high is not None:
            updates.append("alert_threshold_high = ?")
            params.append(request.alert_threshold_high)
        if request.alert_threshold_low is not None:
            updates.append("alert_threshold_low = ?")
            params.append(request.alert_threshold_low)
        if request.alert_watchlist is not None:
            updates.append("alert_watchlist = ?")
            params.append(_json_dump(_normalize_watchlist(request.alert_watchlist)))
        if request.preferred_delivery_source is not None:
            if request.preferred_delivery_source not in VALID_DELIVERY_SOURCES:
                raise UserProfileValidationError(
                    "preferred_delivery_source must be discord or dashboard."
                )
            updates.append("preferred_delivery_source = ?")
            params.append(request.preferred_delivery_source)
        if request.last_active_source is not None:
            if request.last_active_source not in VALID_ACTIVITY_SOURCES:
                raise UserProfileValidationError("last_active_source must be discord or dashboard.")
            updates.append("last_active_source = ?")
            params.append(request.last_active_source)

        if not updates:
            return self.get_or_create_profile(request.user_id)

        updates.append("updated_at = ?")
        params.append(_utc_now())
        params.append(request.user_id)

        with self._connect() as connection:
            connection.execute(
                f"UPDATE user_profiles SET {', '.join(updates)} WHERE user_id = ?",
                tuple(params),
            )
            connection.commit()

        logger.info("Updated alert settings user_id=%s", request.user_id)
        return self.get_or_create_profile(request.user_id)

    def list_profiles_with_alerts_enabled(self) -> list[UserProfileResponse]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM user_profiles WHERE alert_enabled = 1 ORDER BY user_id ASC"
            ).fetchall()
        return [self._row_to_profile(row) for row in rows]

    def list_alert_enabled_user_summaries(self) -> list[AlertEnabledUserResponse]:
        """Return minimal scheduler-friendly records for users with alerts enabled."""
        summaries: list[AlertEnabledUserResponse] = []
        for profile in self.list_profiles_with_alerts_enabled():
            alert_watchlist = profile.alert_watchlist or profile.default_watchlist or list(
                self.default_watchlist
            )
            summaries.append(
                AlertEnabledUserResponse(
                    user_id=profile.user_id,
                    display_name=profile.display_name,
                    preferred_language=profile.preferred_language,
                    alert_enabled=profile.alert_enabled,
                    alert_threshold_high=profile.alert_threshold_high,
                    alert_threshold_low=profile.alert_threshold_low,
                    alert_watchlist=alert_watchlist,
                    preferred_delivery_source=profile.preferred_delivery_source,
                    last_active_source=profile.last_active_source,
                )
            )
        return summaries

    def should_send_alert(self, user_id: str, ticker: str, rule: str, state_key: str) -> bool:
        """Suppress repeated alerts when the same condition keeps firing."""
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM alert_dispatch_history
                WHERE user_id = ? AND ticker = ? AND rule = ?
                """,
                (user_id, ticker, rule),
            ).fetchone()
            if existing and existing["state_key"] == state_key:
                return False

            connection.execute(
                """
                INSERT INTO alert_dispatch_history (user_id, ticker, rule, state_key, last_triggered_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, ticker, rule)
                DO UPDATE SET state_key = excluded.state_key, last_triggered_at = excluded.last_triggered_at
                """,
                (user_id, ticker, rule, state_key, _utc_now()),
            )
            connection.commit()
        return True

    def is_alert_state_new(self, user_id: str, ticker: str, rule: str, state_key: str) -> bool:
        """Return whether a delivery state has not yet been confirmed sent.

        This read-only check is used by external delivery channels. Callers
        record the state only after the channel confirms delivery, allowing a
        transient webhook failure to retry on the next scheduler cycle.
        """
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT state_key FROM alert_dispatch_history
                WHERE user_id = ? AND ticker = ? AND rule = ?
                """,
                (user_id, ticker, rule),
            ).fetchone()
        return not existing or existing["state_key"] != state_key

    def record_alert_dispatched(
        self,
        user_id: str,
        ticker: str,
        rule: str,
        state_key: str,
    ) -> None:
        """Record a state only after an external channel confirms delivery."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO alert_dispatch_history (user_id, ticker, rule, state_key, last_triggered_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, ticker, rule)
                DO UPDATE SET state_key = excluded.state_key, last_triggered_at = excluded.last_triggered_at
                """,
                (user_id, ticker, rule, state_key, _utc_now()),
            )
            connection.commit()


_STORE = UserProfileStore()


def get_user_profile_store() -> UserProfileStore:
    """Return the shared user profile store singleton."""
    return _STORE
