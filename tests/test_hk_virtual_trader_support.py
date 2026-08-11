"""Cross-layer regression tests for HK support in the shared virtual trader."""

from __future__ import annotations

from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

import pandas as pd

from app.services.account_ledger_service import AccountLedgerError, AccountLedgerService
from app.models.account_ledger import (
    VirtualAccountDepositRequest,
    VirtualAccountResetRequest,
)
from app.services.live_virtual_trader import (
    _TRAINING_QUEUE,
    _schedule_background_training_if_enabled,
)
from app.services.model_feedback_service import ModelFeedbackService, _horizon_prices
from app.services.model_lifecycle_service import ModelLifecycleService
from app.services.model_results import (
    _scan_saved_model_artifacts,
    list_compatible_saved_model_candidates,
    resolve_model_artifact_dir,
)


class HkVirtualTraderSupportTests(unittest.TestCase):
    def setUp(self) -> None:
        Path("data").mkdir(exist_ok=True)
        self.db_path = Path(f"data/test_hk_virtual_trader_{uuid4().hex}.db")
        self.artifact_path = Path(f"data/test_hk_models_{uuid4().hex}")
        self.board_lot_patcher = patch(
            "app.services.account_ledger_service.get_hk_board_lot",
            side_effect=lambda ticker: {
                "0005": 400,
                "0700": 100,
                "1810": 200,
                "3690": 100,
                "9988": 100,
            }.get(str(ticker).replace(".HK", "").zfill(4)),
        )
        self.board_lot_lookup = self.board_lot_patcher.start()

    def tearDown(self) -> None:
        self.board_lot_patcher.stop()
        if self.db_path.exists():
            try:
                self.db_path.unlink()
            except PermissionError:
                pass
        _TRAINING_QUEUE.clear()
        if self.artifact_path.exists():
            shutil.rmtree(self.artifact_path, ignore_errors=True)

    def test_us_and_hk_cash_positions_and_currency_do_not_mix(self) -> None:
        service = AccountLedgerService(db_path=str(self.db_path))
        service.create_manual_deposit("u1", 1_000, market="US")
        service.create_manual_deposit("u1", 100_000, market="HK")
        service.create_trade_event(
            user_id="u1", action="buy", ticker="VOO", quantity=1, price=100, market="US"
        )
        service.create_trade_event(
            user_id="u1", action="buy", ticker="700", quantity=100, price=300, market="HK"
        )

        us = service.build_account_summary("u1", latest_prices={"VOO": 110}, market="US")
        hk = service.build_account_summary("u1", latest_prices={"0700": 310}, market="HK")

        self.assertEqual(us["currency"], "USD")
        self.assertEqual(us["currency_symbol"], "$")
        self.assertEqual([item["ticker"] for item in us["holdings"]], ["VOO"])
        self.assertEqual(hk["currency"], "HKD")
        self.assertEqual(hk["currency_symbol"], "HK$")
        self.assertEqual([item["ticker"] for item in hk["holdings"]], ["0700"])
        self.assertEqual(hk["holdings"][0]["board_lot"], 100)

    def test_hk_buys_require_official_metadata_and_a_complete_board_lot(self) -> None:
        service = AccountLedgerService(db_path=str(self.db_path))
        service.create_manual_deposit("u1", 100_000, market="HK")
        with self.assertRaisesRegex(AccountLedgerError, "board lot"):
            service.create_trade_event(
                user_id="u1", action="buy", ticker="0700", quantity=50, price=300, market="HK"
            )
        trade = service.create_trade_event(
            user_id="u1", action="buy", ticker="0005", quantity=400, price=50, market="HK"
        )
        self.assertEqual(trade["ticker"], "0005")
        self.board_lot_lookup.side_effect = lambda _ticker: None
        sale = service.create_trade_event(
            user_id="u1", action="sell", ticker="0005", quantity=400, price=51, market="HK"
        )
        self.assertEqual(sale["event_type"], "sell_trade")
        with self.assertRaisesRegex(AccountLedgerError, "Board-lot metadata"):
            service.create_trade_event(
                user_id="u1", action="buy", ticker="1234", quantity=100, price=1, market="HK"
            )

    def test_account_requests_and_resets_are_market_scoped(self) -> None:
        self.assertEqual(
            VirtualAccountDepositRequest(user_id="u1", amount=1, market="HK").market,
            "HK",
        )
        self.assertEqual(
            VirtualAccountResetRequest(user_id="u1", market="HK").market,
            "HK",
        )

        service = AccountLedgerService(db_path=str(self.db_path))
        service.create_manual_deposit("u1", 1_000, market="US")
        service.create_manual_deposit("u1", 100_000, market="HK")
        service.create_trade_event(
            user_id="u1", action="buy", ticker="VOO", quantity=1, price=100, market="US"
        )
        service.create_trade_event(
            user_id="u1", action="buy", ticker="0700", quantity=100, price=300, market="HK"
        )

        reset_trades = service.reset_profile_trading_activity("u1", market="HK")
        self.assertEqual(reset_trades["market"], "HK")
        self.assertEqual(
            [row["ticker"] for row in service.build_account_summary(
                "u1", latest_prices={"VOO": 100}, market="US"
            )["holdings"]],
            ["VOO"],
        )
        self.assertEqual(
            service.build_account_summary("u1", latest_prices={}, market="HK")["holdings"],
            [],
        )

        reset_hk = service.reset_profile_account_data("u1", market="HK")
        self.assertEqual(reset_hk["currency"], "HKD")
        self.assertEqual(
            service.build_account_summary("u1", latest_prices={}, market="HK")["cash"],
            0,
        )
        self.assertGreater(
            service.build_account_summary(
                "u1", latest_prices={"VOO": 100}, market="US"
            )["total_account_value"],
            0,
        )

    def test_legacy_ledger_rows_migrate_to_us_without_data_loss(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE account_ledger_events (
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
            connection.execute(
                """
                INSERT INTO account_ledger_events (
                    user_id, event_type, amount, created_at, metadata_json
                ) VALUES ('legacy-user', 'manual_deposit', 1234, '2026-01-01T00:00:00+00:00', '{}')
                """
            )
            connection.commit()

        service = AccountLedgerService(db_path=str(self.db_path))
        events = service.list_events("legacy-user", market="US")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["market"], "US")
        self.assertEqual(
            service.build_account_summary("legacy-user", latest_prices={}, market="US")["cash"],
            1234,
        )
        self.assertEqual(
            service.build_account_summary("legacy-user", latest_prices={}, market="HK")["cash"],
            0,
        )

    def test_hk_models_are_namespaced_and_never_reused_for_another_ticker(self) -> None:
        base = self.artifact_path
        try:
            model_0700 = base / "HK" / "0700" / "5y" / "target_5d_return" / "linear_regression"
            model_9988 = base / "HK" / "9988" / "5y" / "target_5d_return" / "linear_regression"
            model_0700.mkdir(parents=True)
            model_9988.mkdir(parents=True)
            (model_0700 / "model.pkl").write_bytes(b"0700")
            (model_9988 / "model.pkl").write_bytes(b"9988")
            _scan_saved_model_artifacts.cache_clear()

            self.assertEqual(
                resolve_model_artifact_dir(
                    "700", "5y", "target_5d_return", "linear_regression", base, market="HK"
                ),
                model_0700,
            )
            candidates = list_compatible_saved_model_candidates(
                "0700", "5y", "target_5d_return", base_dir=base, market="HK"
            )
            self.assertEqual([(row["ticker"], row["model_name"]) for row in candidates], [("0700", "linear_regression")])
            self.assertEqual(
                list_compatible_saved_model_candidates(
                    "0005",
                    "5y",
                    "target_5d_return",
                    requested_model_name="linear_regression",
                    base_dir=base,
                    market="HK",
                ),
                [],
            )
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_market_key_allows_same_ticker_model_identity_in_two_markets(self) -> None:
        service = ModelLifecycleService(db_path=str(self.db_path))
        common = dict(
            ticker="0700",
            period="5y",
            target_name="target_5d_return",
            model_name="linear_regression",
            status="candidate",
            is_validated=False,
            validation_score=0.4,
            stale_after_days=30,
            retrain_type="test",
            metrics_summary={},
            notes="test",
            last_trained_at_utc=None,
            last_evaluated_at_utc=None,
        )
        service._upsert_registry(**common, market="US")
        service._upsert_registry(**common, market="HK")

        self.assertEqual(len(service.list_registry(ticker="0700", market="US")), 1)
        self.assertEqual(len(service.list_registry(ticker="0700", market="HK")), 1)

    def test_hk_feedback_uses_five_observed_sessions_and_stays_market_specific(self) -> None:
        service = ModelFeedbackService(db_path=str(self.db_path))

        def payload(market: str) -> dict:
            return {
                "timestamp": "2026-09-25T08:00:00+00:00",
                "user_id": "u1",
                "market": market,
                "ticker": "0700",
                "action": "buy",
                "quantity": 100,
                "price": 100.0,
                "model_name": "linear_regression",
                "confidence_score": 0.7,
                "metadata": {
                    "market": market,
                    "price_date": "2026-09-25",
                    "prediction_value": 1.0,
                    "task_type": "regression",
                    "model_period": "5y",
                    "model_version": "v1",
                    "model_ticker": "0700",
                    "decision_source": "saved_model",
                },
            }

        self.assertTrue(service.record_decision(payload("US"), benchmark="VOO"))
        self.assertTrue(service.record_decision(payload("HK"), benchmark="2800"))

        dates = pd.to_datetime([
            "2026-09-25", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-02", "2026-10-05"
        ])
        history = pd.DataFrame({"date": dates, "close": [100, 101, 102, 103, 104, 106]})
        self.assertIsNone(_horizon_prices(history.iloc[:5], "2026-09-25", 5))
        self.assertEqual(_horizon_prices(history, "2026-09-25", 5)[0], "2026-10-05")

        def loader(_symbol: str, _period: str) -> pd.DataFrame:
            return history

        result = service.evaluate_pending(price_loader=loader)
        self.assertEqual(result["evaluated"], 2)
        self.assertEqual(service.get_model_summary(
            ticker="0700", model_period="5y", model_name="linear_regression", market="US"
        )["sample_count"], 1)
        self.assertEqual(service.get_model_summary(
            ticker="0700", model_period="5y", model_name="linear_regression", market="HK"
        )["sample_count"], 1)
        self.assertEqual(len(service.list_feedback(market="HK")), 1)

    @patch("app.services.live_virtual_trader.Thread")
    def test_duplicate_lazy_hk_training_is_suppressed(self, thread_mock) -> None:
        _schedule_background_training_if_enabled(
            ticker="0700", period="2y", benchmark="2800", market="HK"
        )
        _schedule_background_training_if_enabled(
            ticker="0700", period="2y", benchmark="2800", market="HK"
        )
        self.assertEqual(thread_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
