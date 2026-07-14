"""SQLite-backed monthly contribution records for user-specific simulations."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.core.settings import get_settings
from app.models.monthly_contribution import (
    MonthlyContributionInputResponse,
    MonthlyContributionRecordResponse,
)
from app.services.user_profile_service import UserProfileStore, get_user_profile_store

logger = logging.getLogger(__name__)

START_MONTH = "2026-04"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _month_key(value: str) -> str:
    text = str(value).strip()
    if len(text) != 7 or text[4] != "-":
        raise ValueError("month must use YYYY-MM format.")
    year = int(text[:4])
    month = int(text[5:7])
    if month < 1 or month > 12:
        raise ValueError("month must use YYYY-MM format.")
    return f"{year:04d}-{month:02d}"


def _month_range(start_month: str, end_month: str) -> list[str]:
    start = _month_key(start_month)
    end = _month_key(end_month)
    start_year, start_m = int(start[:4]), int(start[5:7])
    end_year, end_m = int(end[:4]), int(end[5:7])
    values: list[str] = []
    year, month = start_year, start_m
    while (year, month) <= (end_year, end_m):
        values.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return values


def _current_month_key() -> str:
    now = datetime.now(UTC)
    return f"{now.year:04d}-{now.month:02d}"


def _next_month_key(month_key: str) -> str:
    clean = _month_key(month_key)
    year, month = int(clean[:4]), int(clean[5:7])
    month += 1
    if month > 12:
        year += 1
        month = 1
    return f"{year:04d}-{month:02d}"


class MonthlyContributionStore:
    """Small data-access layer for user-specific monthly contribution records."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = Path(db_path or get_settings().profile_db_path)
        self.profile_store = (
            UserProfileStore(db_path=str(self.db_path))
            if db_path is not None
            else get_user_profile_store()
        )
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
                CREATE TABLE IF NOT EXISTS monthly_contributions (
                    user_id TEXT NOT NULL,
                    month TEXT NOT NULL,
                    amount REAL NOT NULL,
                    locked INTEGER NOT NULL DEFAULT 0,
                    manually_edited INTEGER NOT NULL DEFAULT 0,
                    confirmed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, month)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS monthly_contribution_settings (
                    user_id TEXT PRIMARY KEY,
                    amount REAL NOT NULL,
                    effective_from_month TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # Lightweight migration path for existing local DBs.
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(monthly_contributions)").fetchall()
            }
            if "locked" not in columns:
                connection.execute(
                    "ALTER TABLE monthly_contributions ADD COLUMN locked INTEGER NOT NULL DEFAULT 0"
                )
            if "confirmed_at" not in columns:
                connection.execute("ALTER TABLE monthly_contributions ADD COLUMN confirmed_at TEXT")
            if "manually_edited" not in columns:
                connection.execute(
                    "ALTER TABLE monthly_contributions ADD COLUMN manually_edited INTEGER NOT NULL DEFAULT 0"
                )
            connection.commit()

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (str(table_name).strip(),),
        ).fetchone()
        return row is not None

    def _row_to_record(self, row: sqlite3.Row) -> MonthlyContributionRecordResponse:
        return MonthlyContributionRecordResponse(
            user_id=row["user_id"],
            month=row["month"],
            amount=float(row["amount"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            locked=bool(row["locked"]),
        )

    def _row_to_input(self, row: sqlite3.Row) -> MonthlyContributionInputResponse:
        return MonthlyContributionInputResponse(
            user_id=row["user_id"],
            amount=float(row["amount"]),
            effective_from_month=row["effective_from_month"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _fetch_records(self, user_id: str) -> list[MonthlyContributionRecordResponse]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM monthly_contributions
                WHERE user_id = ?
                ORDER BY month ASC
                """,
                (user_id,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_active_input(self, user_id: str) -> MonthlyContributionInputResponse:
        """Return the active recurring monthly contribution amount for one user."""
        clean_user_id = str(user_id).strip()
        if not clean_user_id:
            raise ValueError("user_id is required.")
        self.profile_store.get_or_create_profile(clean_user_id)
        now = _utc_now()
        current_month = _current_month_key()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM monthly_contribution_settings
                WHERE user_id = ?
                """,
                (clean_user_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO monthly_contribution_settings (
                        user_id, amount, effective_from_month, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (clean_user_id, 0.0, current_month, now, now),
                )
                connection.commit()
                row = connection.execute(
                    """
                    SELECT * FROM monthly_contribution_settings
                    WHERE user_id = ?
                    """,
                    (clean_user_id,),
                ).fetchone()
        if row is None:
            raise ValueError("Failed to load monthly contribution input.")
        return self._row_to_input(row)

    def set_active_input(self, user_id: str, amount: float) -> MonthlyContributionInputResponse:
        """Save a new recurring monthly contribution amount for future monthly cycles."""
        clean_user_id = str(user_id).strip()
        if not clean_user_id:
            raise ValueError("user_id is required.")
        try:
            numeric_amount = float(amount)
        except (TypeError, ValueError) as exc:
            raise ValueError("amount must be numeric.") from exc
        if numeric_amount < 0:
            raise ValueError("amount must be non-negative.")

        self.profile_store.get_or_create_profile(clean_user_id)
        now = _utc_now()
        current_month = _current_month_key()
        next_month = _next_month_key(current_month)

        # The new amount should apply from the next cycle unless the current
        # month has not been applied yet.
        effective_from_month = next_month
        with self._connect() as connection:
            has_current_applied = False
            if self._table_exists(connection, "account_ledger_events"):
                applied_row = connection.execute(
                    """
                    SELECT id
                    FROM account_ledger_events
                    WHERE user_id = ?
                      AND event_type = 'monthly_contribution'
                      AND reference_month = ?
                    LIMIT 1
                    """,
                    (clean_user_id, current_month),
                ).fetchone()
                has_current_applied = applied_row is not None
            if not has_current_applied:
                effective_from_month = current_month

            existing = connection.execute(
                """
                SELECT user_id FROM monthly_contribution_settings
                WHERE user_id = ?
                """,
                (clean_user_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO monthly_contribution_settings (
                        user_id, amount, effective_from_month, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (clean_user_id, numeric_amount, effective_from_month, now, now),
                )
            else:
                connection.execute(
                    """
                    UPDATE monthly_contribution_settings
                    SET amount = ?, effective_from_month = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (numeric_amount, effective_from_month, now, clean_user_id),
                )
            connection.commit()
            row = connection.execute(
                """
                SELECT * FROM monthly_contribution_settings
                WHERE user_id = ?
                """,
                (clean_user_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Failed to save monthly contribution input.")
        logger.info(
            "Saved recurring monthly contribution input user_id=%s amount=%.2f effective_from_month=%s",
            clean_user_id,
            numeric_amount,
            effective_from_month,
        )
        return self._row_to_input(row)

    def get_effective_amount_for_month(self, user_id: str, month: str) -> float:
        """Return the recurring amount that should apply for a given month."""
        clean_month = _month_key(month)
        input_row = self.get_active_input(user_id)
        if clean_month < input_row.effective_from_month:
            return 0.0
        return float(input_row.amount)

    def initialize_for_user(self, user_id: str) -> list[MonthlyContributionRecordResponse]:
        """Ensure records exist from April 2026 through the current month."""
        clean_user_id = str(user_id).strip()
        if not clean_user_id:
            raise ValueError("user_id is required.")
        self.profile_store.get_or_create_profile(clean_user_id)

        all_months = _month_range(START_MONTH, _current_month_key())
        now = _utc_now()
        with self._connect() as connection:
            existing_rows = connection.execute(
                "SELECT month FROM monthly_contributions WHERE user_id = ?",
                (clean_user_id,),
            ).fetchall()
            existing_months = {row["month"] for row in existing_rows}
            missing_months = [month for month in all_months if month not in existing_months]
            for month in missing_months:
                connection.execute(
                    """
                    INSERT INTO monthly_contributions (user_id, month, amount, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (clean_user_id, month, 0.0, now, now),
                )
            connection.commit()

        if missing_months:
            logger.info(
                "Initialized monthly contributions user_id=%s months=%d start_month=%s",
                clean_user_id,
                len(missing_months),
                START_MONTH,
            )
        return self._fetch_records(clean_user_id)

    def list_records(self, user_id: str) -> list[MonthlyContributionRecordResponse]:
        """Return all contribution records in chronological order."""
        clean_user_id = str(user_id).strip()
        if not clean_user_id:
            raise ValueError("user_id is required.")
        self.initialize_for_user(clean_user_id)
        return self._fetch_records(clean_user_id)

    def update_amount(self, user_id: str, month: str, amount: float) -> MonthlyContributionRecordResponse:
        """Update one month's available money."""
        clean_user_id = str(user_id).strip()
        if not clean_user_id:
            raise ValueError("user_id is required.")
        clean_month = _month_key(month)
        if clean_month < START_MONTH:
            raise ValueError(f"month must be {START_MONTH} or later.")
        try:
            numeric_amount = float(amount)
        except (TypeError, ValueError) as exc:
            raise ValueError("amount must be numeric.") from exc
        if numeric_amount < 0:
            raise ValueError("amount must be non-negative.")

        self.initialize_for_user(clean_user_id)
        now = _utc_now()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT locked FROM monthly_contributions
                WHERE user_id = ? AND month = ?
                """,
                (clean_user_id, clean_month),
            ).fetchone()
            if existing is not None and bool(existing["locked"]):
                raise ValueError("This month is already confirmed and cannot be edited.")
            connection.execute(
                """
                UPDATE monthly_contributions
                SET amount = ?, manually_edited = 1, updated_at = ?
                WHERE user_id = ? AND month = ?
                """,
                (numeric_amount, now, clean_user_id, clean_month),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT * FROM monthly_contributions
                WHERE user_id = ? AND month = ?
                """,
                (clean_user_id, clean_month),
            ).fetchone()
        if row is None:
            raise ValueError("Failed to update monthly contribution record.")
        logger.info(
            "Updated monthly contribution user_id=%s month=%s amount=%.2f",
            clean_user_id,
            clean_month,
            numeric_amount,
        )
        return self._row_to_record(row)

    def confirm_amount(self, user_id: str, month: str, amount: float) -> MonthlyContributionRecordResponse:
        """Confirm one monthly planned contribution and lock that month."""
        clean_user_id = str(user_id).strip()
        if not clean_user_id:
            raise ValueError("user_id is required.")
        clean_month = _month_key(month)
        if clean_month < START_MONTH:
            raise ValueError(f"month must be {START_MONTH} or later.")
        try:
            numeric_amount = float(amount)
        except (TypeError, ValueError) as exc:
            raise ValueError("amount must be numeric.") from exc
        if numeric_amount <= 0:
            raise ValueError("amount must be greater than 0.")

        self.initialize_for_user(clean_user_id)
        now = _utc_now()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT locked FROM monthly_contributions
                WHERE user_id = ? AND month = ?
                """,
                (clean_user_id, clean_month),
            ).fetchone()
            if existing is not None and bool(existing["locked"]):
                raise ValueError("This month is already confirmed and immutable.")
            connection.execute(
                """
                UPDATE monthly_contributions
                SET amount = ?, locked = 1, confirmed_at = ?, updated_at = ?
                WHERE user_id = ? AND month = ?
                """,
                (numeric_amount, now, now, clean_user_id, clean_month),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT * FROM monthly_contributions
                WHERE user_id = ? AND month = ?
                """,
                (clean_user_id, clean_month),
            ).fetchone()
        if row is None:
            raise ValueError("Failed to confirm monthly contribution record.")
        logger.info(
            "Confirmed monthly contribution user_id=%s month=%s amount=%.2f",
            clean_user_id,
            clean_month,
            numeric_amount,
        )
        return self._row_to_record(row)

    def get_amount_map(self, user_id: str) -> dict[str, float]:
        """Return a month-to-amount mapping for simulation use.

        Compatibility behavior:
        - base map comes from the active recurring monthly input
        - legacy confirmed monthly plan rows (if present) override base values
        - applied ledger monthly_contribution rows override everything else
        """
        clean_user_id = str(user_id).strip()
        if not clean_user_id:
            raise ValueError("user_id is required.")
        self.profile_store.get_or_create_profile(clean_user_id)

        current_month = _current_month_key()
        month_map: dict[str, float] = {}
        recurring = self.get_active_input(clean_user_id)
        for month in _month_range(START_MONTH, current_month):
            month_map[month] = float(recurring.amount) if month >= recurring.effective_from_month else 0.0

        with self._connect() as connection:
            # Legacy monthly plans (old workflow). Generated placeholder rows
            # have identical create/update timestamps; explicitly edited rows
            # must override the recurring input even when they were not locked.
            if self._table_exists(connection, "monthly_contributions"):
                locked_rows = connection.execute(
                    """
                    SELECT month, amount
                    FROM monthly_contributions
                    WHERE user_id = ?
                      AND (locked = 1 OR manually_edited = 1)
                    """,
                    (clean_user_id,),
                ).fetchall()
                for row in locked_rows:
                    month_map[row["month"]] = float(row["amount"] or 0.0)

            # Applied immutable monthly ledger events are the strongest source.
            if self._table_exists(connection, "account_ledger_events"):
                applied_rows = connection.execute(
                    """
                    SELECT reference_month, amount
                    FROM account_ledger_events
                    WHERE user_id = ?
                      AND event_type = 'monthly_contribution'
                      AND reference_month IS NOT NULL
                    """,
                    (clean_user_id,),
                ).fetchall()
                for row in applied_rows:
                    month_key = row["reference_month"]
                    if not month_key:
                        continue
                    month_map[month_key] = float(row["amount"] or 0.0)
        return month_map


_STORE = MonthlyContributionStore()


def get_monthly_contribution_store() -> MonthlyContributionStore:
    """Return the shared monthly contribution store singleton."""
    return _STORE
