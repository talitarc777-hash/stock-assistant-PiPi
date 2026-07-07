"""Tests for immutable virtual account ledger behavior."""

from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4
import sqlite3
from unittest.mock import patch

from app.services.account_ledger_service import (
    AccountLedgerError,
    AccountLedgerService,
    TRADE_ADMIN_FEE_HKD,
    get_trade_admin_fee_usd,
)
from app.services.monthly_contribution_service import MonthlyContributionStore


class AccountLedgerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path(f"data/test_account_ledger_{uuid4().hex}.db")
        self.service = AccountLedgerService(db_path=str(self.db_path))

    def tearDown(self) -> None:
        if self.db_path.exists():
            try:
                self.db_path.unlink()
            except PermissionError:
                pass

    def test_monthly_contribution_is_immutable(self) -> None:
        self.service.create_monthly_contribution("u1", "2026-04", 1000.0)
        with self.assertRaises(AccountLedgerError):
            self.service.create_monthly_contribution("u1", "2026-04", 1200.0)

    def test_summary_rebuilds_from_ledger_events(self) -> None:
        self.service.create_monthly_contribution("u1", "2026-04", 1000.0)
        self.service.create_manual_deposit("u1", 500.0)
        self.service.create_trade_event(
            user_id="u1",
            action="buy",
            ticker="VOO",
            quantity=3.0,
            price=100.0,
        )
        summary = self.service.build_account_summary("u1", latest_prices={"VOO": 110.0})
        fee_usd = get_trade_admin_fee_usd()
        self.assertAlmostEqual(summary["cash"], 1200.0 - fee_usd, places=6)
        self.assertAlmostEqual(summary["holdings_value"], 330.0, places=6)
        self.assertAlmostEqual(summary["total_account_value"], 1530.0 - fee_usd, places=6)
        self.assertAlmostEqual(summary["unrealized_pnl"], 30.0 - fee_usd, places=6)
        self.assertEqual(len(summary["holdings"]), 1)

    def test_apply_recurring_monthly_contribution_if_due(self) -> None:
        self.service.create_monthly_contribution("u1", "2026-04", 1000.0)
        with patch("app.services.account_ledger_service._current_month", return_value="2026-05"):
            created = self.service.apply_recurring_monthly_contribution_if_due("u1", source="scheduler")
            self.assertIsNotNone(created)
            self.assertEqual(created["reference_month"], "2026-05")
            self.assertAlmostEqual(created["amount"], 1000.0, places=6)

            second_try = self.service.apply_recurring_monthly_contribution_if_due("u1", source="scheduler")
            self.assertIsNone(second_try)

    def test_reset_profile_account_data_is_user_scoped(self) -> None:
        self.service.create_monthly_contribution("u1", "2026-04", 1000.0)
        self.service.create_manual_deposit("u1", 200.0)
        self.service.create_trade_event(
            user_id="u1",
            action="buy",
            ticker="VOO",
            quantity=2.0,
            price=100.0,
        )
        self.service.create_monthly_contribution("u2", "2026-04", 900.0)

        result = self.service.reset_profile_account_data("u1", reset_monthly_contributions=True)
        self.assertTrue(result["reset_completed"])
        self.assertGreaterEqual(result["deleted_ledger_rows"], 1)

        summary_u1 = self.service.build_account_summary("u1", latest_prices={"VOO": 100.0})
        summary_u2 = self.service.build_account_summary("u2", latest_prices={"VOO": 100.0})
        self.assertAlmostEqual(summary_u1["cash"], 0.0, places=6)
        self.assertAlmostEqual(summary_u2["cash"], 900.0, places=6)

    def test_reset_profile_account_data_clears_monthly_store_for_user_only(self) -> None:
        monthly_store = MonthlyContributionStore(db_path=str(self.db_path))
        monthly_store.initialize_for_user("u1")
        monthly_store.update_amount("u1", "2026-04", 1000.0)
        monthly_store.initialize_for_user("u2")
        monthly_store.update_amount("u2", "2026-04", 900.0)

        result = self.service.reset_profile_account_data("u1", reset_monthly_contributions=True)
        self.assertGreaterEqual(result["deleted_monthly_store_rows"], 1)

        with sqlite3.connect(self.db_path) as connection:
            u1_count = connection.execute(
                "SELECT COUNT(1) FROM monthly_contributions WHERE user_id = ?",
                ("u1",),
            ).fetchone()[0]
            u2_count = connection.execute(
                "SELECT COUNT(1) FROM monthly_contributions WHERE user_id = ?",
                ("u2",),
            ).fetchone()[0]
        self.assertEqual(u1_count, 0)
        self.assertGreater(u2_count, 0)

    def test_reset_profile_trading_activity_preserves_funding(self) -> None:
        self.service.create_monthly_contribution("u1", "2026-04", 1000.0)
        self.service.create_manual_deposit("u1", 200.0)
        self.service.create_trade_event(
            user_id="u1",
            action="buy",
            ticker="VOO",
            quantity=2.0,
            price=100.0,
        )

        result = self.service.reset_profile_trading_activity("u1")

        self.assertTrue(result["reset_completed"])
        self.assertEqual(result["deleted_ledger_trade_rows"], 1)
        self.assertEqual(result["preserved_funding_event_rows"], 2)
        summary = self.service.build_account_summary("u1", latest_prices={"VOO": 100.0})
        self.assertAlmostEqual(summary["cash"], 1200.0, places=6)
        self.assertEqual(summary["holdings"], [])

    def test_profile_diagnostics_counts_rows(self) -> None:
        self.service.create_monthly_contribution("u1", "2026-04", 1000.0)
        self.service.create_manual_deposit("u1", 100.0)
        diagnostics = self.service.get_profile_diagnostics("u1")
        self.assertEqual(diagnostics["user_id"], "u1")
        self.assertTrue(diagnostics["loaded_from_storage"])
        self.assertGreaterEqual(diagnostics["ledger_row_count"], 2)

    def test_account_history_includes_running_cash_balance(self) -> None:
        self.service.create_monthly_contribution("u1", "2026-04", 1000.0)
        self.service.create_manual_deposit("u1", 250.0)
        self.service.create_trade_event(
            user_id="u1",
            action="buy",
            ticker="VOO",
            quantity=2.0,
            price=100.0,
        )
        history = self.service.list_account_history("u1", limit=10)
        self.assertEqual(len(history), 3)
        newest = history[0]
        oldest = history[-1]
        self.assertEqual(newest["event_type"], "buy_trade")
        self.assertAlmostEqual(
            newest["cash_balance_after"],
            1050.0 - get_trade_admin_fee_usd(),
            places=6,
        )
        self.assertAlmostEqual(newest["fee_amount"], get_trade_admin_fee_usd(), places=6)
        self.assertAlmostEqual(newest["metadata"]["fee_amount_hkd"], 50.0, places=6)
        self.assertEqual(oldest["event_type"], "monthly_contribution")
        self.assertAlmostEqual(oldest["cash_balance_after"], 1000.0, places=6)

    def test_recent_trade_events_come_from_immutable_ledger(self) -> None:
        self.service.create_monthly_contribution("u1", "2026-04", 1000.0)
        self.service.create_trade_event(
            user_id="u1",
            action="buy",
            ticker="VOO",
            quantity=2.0,
            price=100.0,
        )
        self.service.create_trade_event(
            user_id="u1",
            action="sell",
            ticker="VOO",
            quantity=1.0,
            price=110.0,
        )
        trades = self.service.list_recent_trade_events("u1", limit=5)
        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0]["event_type"], "sell_trade")
        self.assertEqual(trades[1]["event_type"], "buy_trade")
        self.assertAlmostEqual(
            trades[0]["cash_balance_after"],
            910.0 - (2.0 * get_trade_admin_fee_usd()),
            places=6,
        )
        self.assertAlmostEqual(trades[0]["fee_amount"], get_trade_admin_fee_usd(), places=6)

    def test_current_holdings_include_unrealized_percent(self) -> None:
        self.service.create_monthly_contribution("u1", "2026-04", 1000.0)
        self.service.create_trade_event(
            user_id="u1",
            action="buy",
            ticker="VOO",
            quantity=2.0,
            price=100.0,
        )
        holdings = self.service.list_current_holdings("u1", latest_prices={"VOO": 110.0})
        self.assertEqual(len(holdings), 1)
        fee_usd = get_trade_admin_fee_usd()
        expected_cost = 200.0 + fee_usd
        expected_unrealized = 220.0 - expected_cost
        self.assertAlmostEqual(
            holdings[0]["avg_entry_price"],
            expected_cost / 2.0,
            places=6,
        )
        self.assertAlmostEqual(holdings[0]["unrealized_pnl"], expected_unrealized, places=6)
        self.assertAlmostEqual(
            holdings[0]["unrealized_pnl_pct"],
            (expected_unrealized / expected_cost) * 100.0,
            places=6,
        )

    def test_trade_quantity_must_be_at_least_one(self) -> None:
        self.service.create_manual_deposit("u1", 1000.0)
        with self.assertRaisesRegex(AccountLedgerError, "at least 1"):
            self.service.create_trade_event(
                user_id="u1",
                action="buy",
                ticker="VOO",
                quantity=0.5,
                price=100.0,
            )

    def test_trade_quantity_must_be_a_whole_number(self) -> None:
        self.service.create_manual_deposit("u1", 1000.0)
        with self.assertRaisesRegex(AccountLedgerError, "whole number"):
            self.service.create_trade_event(
                user_id="u1",
                action="buy",
                ticker="VOO",
                quantity=1.5,
                price=100.0,
            )

    def test_buy_and_sell_convert_hkd_50_fee_to_usd(self) -> None:
        self.service.create_manual_deposit("u1", 1000.0)
        fee_usd = get_trade_admin_fee_usd()
        buy = self.service.create_trade_event(
            user_id="u1",
            action="buy",
            ticker="VOO",
            quantity=2.0,
            price=100.0,
        )
        self.assertAlmostEqual(buy["amount"], -(200.0 + fee_usd), places=6)
        self.assertAlmostEqual(buy["metadata"]["fee_amount"], fee_usd, places=6)
        self.assertAlmostEqual(
            buy["metadata"]["fee_amount_hkd"],
            TRADE_ADMIN_FEE_HKD,
            places=6,
        )
        self.assertEqual(buy["metadata"]["fee_currency"], "USD")
        self.assertEqual(buy["metadata"]["fee_original_currency"], "HKD")

        sell = self.service.create_trade_event(
            user_id="u1",
            action="sell",
            ticker="VOO",
            quantity=2.0,
            price=150.0,
        )
        self.assertAlmostEqual(sell["amount"], 300.0 - fee_usd, places=6)
        self.assertAlmostEqual(sell["metadata"]["fee_amount"], fee_usd, places=6)

        summary = self.service.build_account_summary("u1")
        self.assertAlmostEqual(summary["cash"], 1100.0 - (2.0 * fee_usd), places=6)
        self.assertAlmostEqual(summary["realized_pnl"], 100.0 - (2.0 * fee_usd), places=6)

    def test_partial_integer_sell_preserves_remaining_holding(self) -> None:
        self.service.create_manual_deposit("u1", 2000.0)
        self.service.create_trade_event(
            user_id="u1",
            action="buy",
            ticker="VOO",
            quantity=8,
            price=100.0,
        )
        self.service.create_trade_event(
            user_id="u1",
            action="sell",
            ticker="VOO",
            quantity=5,
            price=110.0,
        )

        holdings = self.service.list_current_holdings("u1", latest_prices={"VOO": 110.0})
        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0]["quantity"], 3.0)

        trades = self.service.list_recent_trade_events("u1", limit=5)
        self.assertEqual(trades[0]["event_type"], "sell_trade")
        self.assertEqual(trades[0]["remaining_quantity"], 3.0)
        self.assertEqual(trades[1]["event_type"], "buy_trade")
        self.assertEqual(trades[1]["remaining_quantity"], 8.0)


if __name__ == "__main__":
    unittest.main()
