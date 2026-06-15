"""Immutable virtual account ledger and derived-account helpers.

This service is intentionally append-only:
- historical events are never updated in place
- account cash/holdings are rebuilt from ledger records
- corrections should be done as compensating events
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from app.core.settings import get_settings
from app.models.account_ledger import LEDGER_EVENT_TYPES
from app.services.market_data import get_price_history
from app.services.monthly_contribution_service import (
    START_MONTH,
    MonthlyContributionStore,
    get_monthly_contribution_store,
)

logger = logging.getLogger(__name__)

MIN_TRADE_QUANTITY = 1
TRADE_ADMIN_FEE_HKD = 50.0


class AccountLedgerError(Exception):
    """Raised when immutable ledger operations fail validation."""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _clean_user_id(user_id: str) -> str:
    value = str(user_id).strip()
    if not value:
        raise AccountLedgerError("user_id is required.")
    return value


def _clean_month(month: str) -> str:
    value = str(month).strip()
    if len(value) != 7 or value[4] != "-":
        raise AccountLedgerError("month must use YYYY-MM format.")
    year = int(value[:4])
    m = int(value[5:7])
    if m < 1 or m > 12:
        raise AccountLedgerError("month must use YYYY-MM format.")
    normalized = f"{year:04d}-{m:02d}"
    if normalized < START_MONTH:
        raise AccountLedgerError(f"month must be {START_MONTH} or later.")
    return normalized


def _month_range(start_month: str, end_month: str) -> list[str]:
    start = _clean_month(start_month)
    end = _clean_month(end_month)
    sy, sm = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    values: list[str] = []
    year, month = sy, sm
    while (year, month) <= (ey, em):
        values.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year += 1
            month = 1
    return values


def _current_month() -> str:
    now = datetime.now(UTC)
    return f"{now.year:04d}-{now.month:02d}"


@dataclass
class _HoldingState:
    ticker: str
    quantity: float
    avg_entry_price: float
    realized_pnl: float = 0.0


class AccountLedgerService:
    """SQLite-backed append-only ledger for virtual account simulation."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = Path(db_path or get_settings().profile_db_path)
        # Keep monthly-planning reads scoped to the same database path when a
        # custom DB is used (for tests/local sandbox runs).
        self.monthly_contribution_store = (
            get_monthly_contribution_store()
            if db_path is None
            else MonthlyContributionStore(db_path=str(self.db_path))
        )
        # Tiny in-process price cache to reduce repeated yfinance calls under
        # constrained hosts (for example Railway free-tier containers).
        self._latest_price_cache: dict[str, tuple[float, float]] = {}
        self._latest_price_cache_lock = Lock()
        self._latest_price_ttl_seconds = 120.0
        self._initialize()

    def _get_latest_price_cached(self, ticker: str) -> float:
        clean_ticker = str(ticker).strip().upper()
        now_ts = datetime.now(UTC).timestamp()
        with self._latest_price_cache_lock:
            cached = self._latest_price_cache.get(clean_ticker)
            if cached is not None:
                value, expires_at = cached
                if expires_at > now_ts:
                    return float(value)
                self._latest_price_cache.pop(clean_ticker, None)
        df = get_price_history(clean_ticker, period="3mo")
        latest_close = float(df.sort_values("date").iloc[-1]["close"])
        with self._latest_price_cache_lock:
            self._latest_price_cache[clean_ticker] = (
                latest_close,
                now_ts + self._latest_price_ttl_seconds,
            )
        return latest_close

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (str(table_name).strip(),),
        ).fetchone()
        return row is not None

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS account_ledger_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    amount REAL NOT NULL,
                    ticker TEXT,
                    quantity REAL,
                    price REAL,
                    reason TEXT,
                    source TEXT,
                    reference_month TEXT,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_unique_monthly
                ON account_ledger_events(user_id, event_type, reference_month)
                WHERE event_type = 'monthly_contribution'
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ledger_user_created
                ON account_ledger_events(user_id, created_at, id)
                """
            )
            conn.commit()

    def _insert_event(
        self,
        *,
        user_id: str,
        event_type: str,
        amount: float,
        ticker: str | None = None,
        quantity: float | None = None,
        price: float | None = None,
        reason: str | None = None,
        source: str | None = None,
        reference_month: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        clean_user_id = _clean_user_id(user_id)
        normalized_type = str(event_type).strip().lower()
        if normalized_type not in LEDGER_EVENT_TYPES:
            raise AccountLedgerError(f"Unsupported event_type: {event_type}.")
        if not isinstance(amount, (int, float)):
            raise AccountLedgerError("amount must be numeric.")

        payload_created_at = created_at or _utc_now()
        payload_metadata = metadata or {}
        payload_month = _clean_month(reference_month) if reference_month else None
        normalized_ticker = str(ticker).strip().upper() if ticker else None

        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO account_ledger_events (
                        user_id, event_type, amount, ticker, quantity, price, reason,
                        source, reference_month, created_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clean_user_id,
                        normalized_type,
                        float(amount),
                        normalized_ticker,
                        float(quantity) if quantity is not None else None,
                        float(price) if price is not None else None,
                        (reason or "").strip() or None,
                        (source or "").strip() or None,
                        payload_month,
                        payload_created_at,
                        json.dumps(payload_metadata, ensure_ascii=False),
                    ),
                )
                event_id = int(cursor.lastrowid)
                conn.commit()
        except sqlite3.IntegrityError as exc:
            if normalized_type == "monthly_contribution":
                raise AccountLedgerError(
                    "This monthly contribution already exists and is immutable."
                ) from exc
            raise AccountLedgerError("Failed to persist ledger event.") from exc

        logger.info(
            "Ledger event created user_id=%s type=%s amount=%.2f ticker=%s month=%s source=%s",
            clean_user_id,
            normalized_type,
            float(amount),
            normalized_ticker or "",
            payload_month or "",
            source or "",
        )
        return self.get_event_by_id(event_id)

    def get_event_by_id(self, event_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM account_ledger_events WHERE id = ?",
                (int(event_id),),
            ).fetchone()
        if row is None:
            raise AccountLedgerError("Ledger event was not found.")
        return self._row_to_dict(row)

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        return {
            "id": int(row["id"]),
            "user_id": row["user_id"],
            "event_type": row["event_type"],
            "amount": float(row["amount"]),
            "ticker": row["ticker"],
            "quantity": float(row["quantity"]) if row["quantity"] is not None else None,
            "price": float(row["price"]) if row["price"] is not None else None,
            "reason": row["reason"],
            "source": row["source"],
            "reference_month": row["reference_month"],
            "created_at": row["created_at"],
            "metadata": metadata,
        }

    def list_events(
        self,
        user_id: str,
        limit: int = 200,
        offset: int = 0,
        event_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        clean_user_id = _clean_user_id(user_id)
        sql = "SELECT * FROM account_ledger_events WHERE user_id = ?"
        params: list[Any] = [clean_user_id]
        if event_types:
            normalized = [item.strip().lower() for item in event_types if item.strip()]
            if normalized:
                placeholders = ",".join("?" for _ in normalized)
                sql += f" AND event_type IN ({placeholders})"
                params.extend(normalized)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.append(max(1, int(limit)))
        params.append(max(0, int(offset)))
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_account_history(
        self,
        user_id: str,
        limit: int = 200,
        offset: int = 0,
        event_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return immutable account history rows with running balance context.

        This is the canonical transaction-history view for the web app:
        - oldest-first ledger replay computes `cash_balance_after`
        - newest rows are returned first for easier trading-app style display
        - trade rows expose gross/net cash values without mutating ledger history
        """
        normalized_filter = {
            item.strip().lower()
            for item in (event_types or [])
            if str(item).strip()
        }
        chronological_events = self.list_events_chronological(user_id=user_id)
        running_cash = 0.0
        history_rows: list[dict[str, Any]] = []
        for event in chronological_events:
            net_amount = float(event["amount"])
            running_cash += net_amount
            quantity = event.get("quantity")
            price = event.get("price")
            metadata = dict(event.get("metadata") or {})
            fee_amount = float(metadata.get("fee_amount") or 0.0)
            gross_amount = None
            if quantity is not None and price is not None:
                gross_amount = float(quantity) * float(price)
            history_row = {
                "id": int(event["id"]),
                "user_id": event["user_id"],
                "event_type": event["event_type"],
                "created_at": event["created_at"],
                "ticker": event.get("ticker"),
                "quantity": quantity,
                "price": price,
                "gross_amount": gross_amount,
                "fee_amount": fee_amount,
                "net_amount": net_amount,
                "cash_change": net_amount,
                "cash_balance_after": running_cash,
                "reason": event.get("reason"),
                "source": event.get("source"),
                "reference_month": event.get("reference_month"),
                "metadata": metadata,
            }
            if not normalized_filter or history_row["event_type"] in normalized_filter:
                history_rows.append(history_row)

        newest_first = list(reversed(history_rows))
        logger.info(
            "Account history rebuilt user_id=%s events=%d returned=%d",
            _clean_user_id(user_id),
            len(history_rows),
            min(len(newest_first), max(1, int(limit))),
        )
        safe_offset = max(0, int(offset))
        safe_limit = max(1, int(limit))
        return newest_first[safe_offset : safe_offset + safe_limit]

    def list_events_chronological(
        self,
        user_id: str,
        event_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return immutable ledger events oldest-first for rebuilding curves/state."""
        clean_user_id = _clean_user_id(user_id)
        sql = "SELECT * FROM account_ledger_events WHERE user_id = ?"
        params: list[Any] = [clean_user_id]
        if event_types:
            normalized = [item.strip().lower() for item in event_types if item.strip()]
            if normalized:
                placeholders = ",".join("?" for _ in normalized)
                sql += f" AND event_type IN ({placeholders})"
                params.extend(normalized)
        sql += " ORDER BY created_at ASC, id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def create_monthly_contribution(
        self,
        user_id: str,
        month: str,
        amount: float,
        source: str = "web",
        reason: str | None = None,
    ) -> dict[str, Any]:
        normalized_month = _clean_month(month)
        numeric_amount = float(amount)
        if numeric_amount <= 0:
            raise AccountLedgerError("amount must be greater than 0.")
        return self._insert_event(
            user_id=user_id,
            event_type="monthly_contribution",
            amount=numeric_amount,
            reason=reason or "monthly contribution",
            source=source,
            reference_month=normalized_month,
            metadata={"month": normalized_month},
        )

    def create_manual_deposit(
        self,
        user_id: str,
        amount: float,
        source: str = "web",
        reason: str | None = None,
    ) -> dict[str, Any]:
        numeric_amount = float(amount)
        if numeric_amount <= 0:
            raise AccountLedgerError("amount must be greater than 0.")
        return self._insert_event(
            user_id=user_id,
            event_type="manual_deposit",
            amount=numeric_amount,
            reason=reason or "manual deposit",
            source=source,
        )

    def create_withdrawal(
        self,
        user_id: str,
        amount: float,
        source: str = "web",
        reason: str | None = None,
    ) -> dict[str, Any]:
        numeric_amount = float(amount)
        if numeric_amount <= 0:
            raise AccountLedgerError("amount must be greater than 0.")

        summary = self.build_account_summary(user_id=user_id)
        if summary["cash"] < numeric_amount:
            raise AccountLedgerError("Insufficient cash for withdrawal.")
        return self._insert_event(
            user_id=user_id,
            event_type="withdrawal",
            amount=-numeric_amount,
            reason=reason or "withdrawal",
            source=source,
        )

    def create_trade_event(
        self,
        *,
        user_id: str,
        action: str,
        ticker: str,
        quantity: float,
        price: float,
        source: str = "trader",
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_action = str(action).strip().lower()
        if normalized_action not in {"buy", "sell"}:
            raise AccountLedgerError("trade action must be buy or sell.")
        numeric_quantity = float(quantity)
        numeric_price = float(price)
        if not math.isfinite(numeric_quantity) or numeric_quantity < MIN_TRADE_QUANTITY:
            raise AccountLedgerError(
                f"quantity must be at least {MIN_TRADE_QUANTITY:g}."
            )
        if not numeric_quantity.is_integer():
            raise AccountLedgerError("quantity must be a whole number.")
        numeric_quantity = float(int(numeric_quantity))
        if numeric_price <= 0:
            raise AccountLedgerError("price must be greater than 0.")

        gross_amount = numeric_quantity * numeric_price
        fee_amount = TRADE_ADMIN_FEE_HKD
        if normalized_action == "buy":
            total_cost = gross_amount + fee_amount
            clean_user_id = _clean_user_id(user_id)
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT COALESCE(SUM(amount), 0) AS cash
                    FROM account_ledger_events
                    WHERE user_id = ?
                    """,
                    (clean_user_id,),
                ).fetchone()
            cash_available = float(row["cash"] if row is not None else 0.0)
            if cash_available < total_cost:
                raise AccountLedgerError(
                    "Insufficient cash for trade value and HKD 50 administrative cost."
                )
            amount = -total_cost
        else:
            holdings, _ = self._rebuild_holdings(user_id)
            holding = holdings.get(str(ticker).strip().upper())
            if holding is None or holding.quantity + 1e-8 < numeric_quantity:
                raise AccountLedgerError("Insufficient holding quantity for sell trade.")
            amount = gross_amount - fee_amount

        event_type = "buy_trade" if normalized_action == "buy" else "sell_trade"
        event_metadata = dict(metadata or {})
        event_metadata.update(
            {
                "fee_amount": fee_amount,
                "gross_amount": gross_amount,
                "minimum_trade_quantity": MIN_TRADE_QUANTITY,
            }
        )
        return self._insert_event(
            user_id=user_id,
            event_type=event_type,
            amount=amount,
            ticker=ticker,
            quantity=numeric_quantity,
            price=numeric_price,
            reason=reason or f"{normalized_action} trade",
            source=source,
            metadata=event_metadata,
        )

    def list_monthly_contribution_records(self, user_id: str) -> list[dict[str, Any]]:
        clean_user_id = _clean_user_id(user_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT reference_month, amount, created_at, id
                FROM account_ledger_events
                WHERE user_id = ? AND event_type = 'monthly_contribution'
                ORDER BY reference_month ASC, id ASC
                """,
                (clean_user_id,),
            ).fetchall()
        seen: set[str] = set()
        records: list[dict[str, Any]] = []
        for row in rows:
            month = row["reference_month"]
            if not month or month in seen:
                continue
            seen.add(month)
            records.append(
                {
                    "month": month,
                    "amount": float(row["amount"]),
                    "created_at": row["created_at"],
                    "locked": True,
                }
            )
        return records

    def apply_recurring_monthly_contribution_if_due(
        self,
        user_id: str,
        source: str = "scheduler",
    ) -> dict[str, Any] | None:
        """Auto-create the current-month contribution when monthly recurrence is due.

        Recurrence rule:
        - if current month already has a monthly_contribution event: do nothing
        - else use the active recurring monthly contribution amount
        - compatibility fallback: latest prior applied monthly amount
        """
        clean_user_id = _clean_user_id(user_id)
        current_month = _current_month()

        with self._connect() as conn:
            existing = conn.execute(
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
            if existing is not None:
                return None

        carry_amount = float(
            self.monthly_contribution_store.get_effective_amount_for_month(
                clean_user_id,
                current_month,
            )
        )
        if carry_amount <= 0:
            with self._connect() as conn:
                latest_prior = conn.execute(
                    """
                    SELECT reference_month, amount
                    FROM account_ledger_events
                    WHERE user_id = ?
                      AND event_type = 'monthly_contribution'
                      AND reference_month IS NOT NULL
                      AND reference_month < ?
                    ORDER BY reference_month DESC, id DESC
                    LIMIT 1
                    """,
                    (clean_user_id, current_month),
                ).fetchone()
            if latest_prior is None:
                return None
            carry_amount = float(latest_prior["amount"] or 0.0)
            if carry_amount <= 0:
                return None

        event = self.create_monthly_contribution(
            user_id=clean_user_id,
            month=current_month,
            amount=carry_amount,
            source=source,
            reason="auto recurring monthly contribution",
        )
        logger.info(
            "Auto-applied recurring monthly contribution user_id=%s month=%s amount=%.2f",
            clean_user_id,
            current_month,
            carry_amount,
        )
        logger.info(
            "monthly_contribution_applied profile_id=%s month=%s amount=%.2f",
            clean_user_id,
            current_month,
            carry_amount,
        )
        return event

    def build_monthly_contribution_view(self, user_id: str) -> list[dict[str, Any]]:
        clean_user_id = _clean_user_id(user_id)
        planned_records = {
            item.month: item
            for item in self.monthly_contribution_store.list_records(clean_user_id)
        }
        applied_months = {row["month"] for row in self.list_monthly_contribution_records(clean_user_id)}
        rows: list[dict[str, Any]] = []
        for month in _month_range(START_MONTH, _current_month()):
            if month in planned_records:
                row = planned_records[month]
                rows.append(
                    {
                        "user_id": clean_user_id,
                        "month": month,
                        "amount": float(row.amount),
                        "created_at": row.created_at,
                        "updated_at": row.updated_at,
                        "locked": bool(row.locked),
                        "applied_to_cash": month in applied_months,
                    }
                )
            else:
                rows.append(
                    {
                        "user_id": clean_user_id,
                        "month": month,
                        "amount": 0.0,
                        "created_at": "",
                        "updated_at": "",
                        "locked": False,
                        "applied_to_cash": False,
                    }
                )
        return rows

    def _rebuild_holdings(self, user_id: str) -> tuple[dict[str, _HoldingState], float]:
        clean_user_id = _clean_user_id(user_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM account_ledger_events
                WHERE user_id = ? AND event_type IN ('buy_trade', 'sell_trade')
                ORDER BY id ASC
                """,
                (clean_user_id,),
            ).fetchall()

        holdings: dict[str, _HoldingState] = {}
        realized_total = 0.0
        for row in rows:
            ticker = (row["ticker"] or "").upper()
            if not ticker:
                continue
            qty = float(row["quantity"] or 0.0)
            price = float(row["price"] or 0.0)
            if qty <= 0 or price <= 0:
                continue
            state = holdings.get(ticker) or _HoldingState(ticker=ticker, quantity=0.0, avg_entry_price=0.0)
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            fee_amount = float(metadata.get("fee_amount") or 0.0)
            if row["event_type"] == "buy_trade":
                new_qty = state.quantity + qty
                new_avg = (
                    (
                        (state.quantity * state.avg_entry_price)
                        + (qty * price)
                        + fee_amount
                    )
                    / new_qty
                    if new_qty > 0
                    else 0.0
                )
                state.quantity = new_qty
                state.avg_entry_price = new_avg
            else:
                sell_qty = min(qty, state.quantity)
                if sell_qty <= 0:
                    continue
                realized = ((price - state.avg_entry_price) * sell_qty) - fee_amount
                realized_total += realized
                state.quantity -= sell_qty
                if state.quantity <= 1e-8:
                    state.quantity = 0.0
                    state.avg_entry_price = 0.0
            holdings[ticker] = state
        holdings = {key: value for key, value in holdings.items() if value.quantity > 0}
        return holdings, realized_total

    def list_current_holdings(
        self,
        user_id: str,
        latest_prices: dict[str, float] | None = None,
    ) -> list[dict[str, Any]]:
        """Return current open holdings rebuilt from immutable buy/sell events."""
        summary = self.build_account_summary(user_id=user_id, latest_prices=latest_prices)
        return summary["holdings"]

    def list_recent_trade_events(
        self,
        user_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return recent executed buy/sell trades derived from immutable ledger rows."""
        history_rows = self.list_account_history(
            user_id=user_id,
            limit=max(limit * 3, limit),
            event_types=["buy_trade", "sell_trade"],
        )
        trades: list[dict[str, Any]] = []
        for row in history_rows:
            quantity = float(row.get("quantity") or 0.0)
            price = float(row.get("price") or 0.0)
            if quantity <= 0 or price <= 0:
                continue
            trades.append(
                {
                    "id": int(row["id"]),
                    "user_id": row["user_id"],
                    "created_at": row["created_at"],
                    "event_type": row["event_type"],
                    "ticker": str(row.get("ticker") or "").upper(),
                    "quantity": quantity,
                    "price": price,
                    "gross_amount": float(row.get("gross_amount") or (quantity * price)),
                    "fee_amount": float(row.get("fee_amount") or 0.0),
                    "net_amount": float(row["net_amount"]),
                    "cash_balance_after": float(row["cash_balance_after"]),
                    "reason": row.get("reason"),
                    "source": row.get("source"),
                    "metadata": dict(row.get("metadata") or {}),
                }
            )
            if len(trades) >= max(1, int(limit)):
                break
        return trades

    def build_account_summary(
        self,
        user_id: str,
        latest_prices: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        clean_user_id = _clean_user_id(user_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(amount), 0) AS cash
                FROM account_ledger_events
                WHERE user_id = ?
                """,
                (clean_user_id,),
            ).fetchone()
            cash_value = float(row["cash"] if row is not None else 0.0)

            net_row = conn.execute(
                """
                SELECT COALESCE(SUM(amount), 0) AS net_deposits
                FROM account_ledger_events
                WHERE user_id = ?
                  AND event_type IN ('monthly_contribution', 'manual_deposit', 'withdrawal')
                """,
                (clean_user_id,),
            ).fetchone()
            net_deposits = float(net_row["net_deposits"] if net_row is not None else 0.0)

        holdings_map, realized_pnl = self._rebuild_holdings(clean_user_id)
        price_map = dict(latest_prices or {})
        if holdings_map:
            missing = [ticker for ticker in holdings_map if ticker not in price_map]
            for ticker in missing:
                price_map[ticker] = self._get_latest_price_cached(ticker)

        holdings_rows: list[dict[str, Any]] = []
        holdings_value = 0.0
        unrealized_total = 0.0
        for ticker, state in sorted(holdings_map.items()):
            market_price = float(price_map.get(ticker, 0.0))
            market_value = state.quantity * market_price
            unrealized = (market_price - state.avg_entry_price) * state.quantity
            holdings_value += market_value
            unrealized_total += unrealized
            holdings_rows.append(
                {
                    "ticker": ticker,
                    "quantity": state.quantity,
                    "avg_entry_price": state.avg_entry_price,
                    "current_price": market_price,
                    "market_value": market_value,
                    "unrealized_pnl": unrealized,
                    "unrealized_pnl_pct": (
                        ((market_price - state.avg_entry_price) / state.avg_entry_price) * 100.0
                        if state.avg_entry_price > 0
                        else None
                    ),
                    "latest_signal": None,
                }
            )

        snapshot_time = _utc_now()
        logger.info(
            "Account summary rebuilt user_id=%s cash=%.2f holdings_value=%.2f total_equity=%.2f realized=%.2f unrealized=%.2f holdings=%d",
            clean_user_id,
            cash_value,
            holdings_value,
            cash_value + holdings_value,
            realized_pnl,
            unrealized_total,
            len(holdings_rows),
        )

        return {
            "user_id": clean_user_id,
            "as_of": snapshot_time,
            "last_updated": snapshot_time,
            # The live equity curve appends the same snapshot as its latest point,
            # so this timestamp is the canonical trust signal for summary/chart sync.
            "curve_last_point_timestamp": snapshot_time,
            "cash": cash_value,
            "holdings_value": holdings_value,
            "total_account_value": cash_value + holdings_value,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_total,
            "net_deposits": net_deposits,
            "holdings": holdings_rows,
            "latest_prices": price_map,
        }

    def reset_profile_account_data(
        self,
        user_id: str,
        *,
        reset_monthly_contributions: bool = True,
    ) -> dict[str, Any]:
        """Delete one profile's simulated account records from persistent storage.

        This is intentionally profile-scoped and destructive. It does not touch
        other profiles or any model artifact files.
        """
        clean_user_id = _clean_user_id(user_id)
        deleted_ledger_rows = 0
        deleted_monthly_contribution_rows = 0
        deleted_live_trade_rows = 0
        deleted_live_position_rows = 0
        deleted_trader_cash_rows = 0
        deleted_trader_contribution_rows = 0
        deleted_monthly_store_rows = 0
        deleted_monthly_input_rows = 0

        with self._connect() as conn:
            # Optional monthly-contribution reset:
            # - if False, preserve recurring monthly_contribution events
            # - always remove deposits/withdrawals/trades for a fresh account state
            if reset_monthly_contributions:
                monthly_before = conn.execute(
                    """
                    SELECT COUNT(1) AS cnt
                    FROM account_ledger_events
                    WHERE user_id = ? AND event_type = 'monthly_contribution'
                    """,
                    (clean_user_id,),
                ).fetchone()
                cursor = conn.execute(
                    "DELETE FROM account_ledger_events WHERE user_id = ?",
                    (clean_user_id,),
                )
                deleted_ledger_rows = int(cursor.rowcount or 0)
                deleted_monthly_contribution_rows = (
                    0 if monthly_before is None else int(monthly_before["cnt"] or 0)
                )
            else:
                cursor = conn.execute(
                    """
                    DELETE FROM account_ledger_events
                    WHERE user_id = ?
                      AND event_type IN ('manual_deposit', 'withdrawal', 'buy_trade', 'sell_trade', 'fee')
                    """,
                    (clean_user_id,),
                )
                deleted_ledger_rows = int(cursor.rowcount or 0)
                cursor_mc = conn.execute(
                    """
                    SELECT COUNT(1) AS cnt
                    FROM account_ledger_events
                    WHERE user_id = ? AND event_type = 'monthly_contribution'
                    """,
                    (clean_user_id,),
                ).fetchone()
                deleted_monthly_contribution_rows = 0 if cursor_mc is None else 0

            # Live trader persistent tables (if present).
            if self._table_exists(conn, "live_trader_trade_log"):
                cursor = conn.execute(
                    "DELETE FROM live_trader_trade_log WHERE user_id = ?",
                    (clean_user_id,),
                )
                deleted_live_trade_rows = int(cursor.rowcount or 0)
            if self._table_exists(conn, "live_trader_positions"):
                cursor = conn.execute(
                    "DELETE FROM live_trader_positions WHERE user_id = ?",
                    (clean_user_id,),
                )
                deleted_live_position_rows = int(cursor.rowcount or 0)

            # Legacy trader cash service tables (if present).
            if self._table_exists(conn, "live_trader_accounts"):
                cursor = conn.execute(
                    "DELETE FROM live_trader_accounts WHERE user_id = ?",
                    (clean_user_id,),
                )
                deleted_trader_cash_rows = int(cursor.rowcount or 0)
            if self._table_exists(conn, "live_trader_monthly_contributions"):
                cursor = conn.execute(
                    "DELETE FROM live_trader_monthly_contributions WHERE user_id = ?",
                    (clean_user_id,),
                )
                deleted_trader_contribution_rows = int(cursor.rowcount or 0)

            # Separate monthly contribution store used by historical simulation
            # APIs. This must be cleared as part of a true profile reset.
            if self._table_exists(conn, "monthly_contributions"):
                cursor = conn.execute(
                    "DELETE FROM monthly_contributions WHERE user_id = ?",
                    (clean_user_id,),
                )
                deleted_monthly_store_rows = int(cursor.rowcount or 0)
            if self._table_exists(conn, "monthly_contribution_settings"):
                cursor = conn.execute(
                    "DELETE FROM monthly_contribution_settings WHERE user_id = ?",
                    (clean_user_id,),
                )
                deleted_monthly_input_rows = int(cursor.rowcount or 0)

            conn.commit()

        logger.warning(
            "Virtual account reset user_id=%s ledger=%d live_trades=%d live_positions=%d trader_cash=%d trader_monthly=%d monthly_store=%d monthly_input=%d reset_monthly_contributions=%s",
            clean_user_id,
            deleted_ledger_rows,
            deleted_live_trade_rows,
            deleted_live_position_rows,
            deleted_trader_cash_rows,
            deleted_trader_contribution_rows,
            deleted_monthly_store_rows,
            deleted_monthly_input_rows,
            bool(reset_monthly_contributions),
        )
        return {
            "user_id": clean_user_id,
            "reset_completed": True,
            "deleted_ledger_rows": deleted_ledger_rows,
            "deleted_live_trade_rows": deleted_live_trade_rows,
            "deleted_live_position_rows": deleted_live_position_rows,
            "deleted_trader_cash_rows": deleted_trader_cash_rows,
            "deleted_trader_contribution_rows": deleted_trader_contribution_rows,
            "deleted_monthly_contribution_rows": deleted_monthly_contribution_rows,
            "deleted_monthly_store_rows": deleted_monthly_store_rows,
            "deleted_monthly_input_rows": deleted_monthly_input_rows,
            "message": "Reset completed for this profile.",
        }

    def get_profile_diagnostics(self, user_id: str) -> dict[str, Any]:
        """Return profile-scoped persistence diagnostics for troubleshooting."""
        clean_user_id = _clean_user_id(user_id)
        summary = self.build_account_summary(clean_user_id)
        with self._connect() as conn:
            ledger_row = conn.execute(
                "SELECT COUNT(1) AS cnt FROM account_ledger_events WHERE user_id = ?",
                (clean_user_id,),
            ).fetchone()
            trade_row = None
            position_row = None
            if self._table_exists(conn, "live_trader_trade_log"):
                trade_row = conn.execute(
                    "SELECT COUNT(1) AS cnt FROM live_trader_trade_log WHERE user_id = ?",
                    (clean_user_id,),
                ).fetchone()
            if self._table_exists(conn, "live_trader_positions"):
                position_row = conn.execute(
                    "SELECT COUNT(1) AS cnt FROM live_trader_positions WHERE user_id = ?",
                    (clean_user_id,),
                ).fetchone()
            monthly_row = conn.execute(
                """
                SELECT COUNT(1) AS cnt
                FROM account_ledger_events
                WHERE user_id = ? AND event_type = 'monthly_contribution'
                """,
                (clean_user_id,),
            ).fetchone()

        return {
            "user_id": clean_user_id,
            "loaded_from_storage": True,
            "ledger_row_count": 0 if ledger_row is None else int(ledger_row["cnt"] or 0),
            "trade_row_count": 0 if trade_row is None else int(trade_row["cnt"] or 0),
            "position_row_count": 0 if position_row is None else int(position_row["cnt"] or 0),
            "monthly_contribution_row_count": 0 if monthly_row is None else int(monthly_row["cnt"] or 0),
            "cash": float(summary["cash"]),
            "holdings_count": len(summary["holdings"]),
            "total_account_value": float(summary["total_account_value"]),
            "as_of": summary["as_of"],
        }


_SERVICE = AccountLedgerService()


def get_account_ledger_service() -> AccountLedgerService:
    return _SERVICE
