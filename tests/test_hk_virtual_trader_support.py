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
    _reset_background_training_queue_for_tests,
    _decision_model_reporting,
    _resolve_user_tickers,
    _schedule_background_training_if_enabled,
    ensure_active_ticker_model_training,
)
from app.services.model_feedback_service import ModelFeedbackService, _horizon_prices
from app.services.model_lifecycle_service import (
    VALIDATION_GATE_VERSION,
    ModelLifecycleService,
)
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
        _reset_background_training_queue_for_tests()
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
            model_global = base / "HK" / "GLOBAL" / "5y" / "target_5d_return" / "ridge_regression"
            model_0700.mkdir(parents=True)
            model_9988.mkdir(parents=True)
            model_global.mkdir(parents=True)
            (model_0700 / "model.pkl").write_bytes(b"0700")
            (model_9988 / "model.pkl").write_bytes(b"9988")
            (model_global / "model.pkl").write_bytes(b"global")
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
            self.assertEqual(
                [(row["ticker"], row["model_name"]) for row in candidates],
                [("0700", "linear_regression"), ("GLOBAL", "ridge_regression")],
            )
            self.assertEqual(
                [(row["ticker"], row["model_name"]) for row in list_compatible_saved_model_candidates(
                    "0005",
                    "5y",
                    "target_5d_return",
                    requested_model_name="linear_regression",
                    base_dir=base,
                    market="HK",
                )],
                [("GLOBAL", "ridge_regression")],
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

    def test_stale_hk_model_is_not_selected_by_auto_best(self) -> None:
        service = ModelLifecycleService(db_path=str(self.db_path))
        service._upsert_registry(
            ticker="0700",
            period="2y",
            target_name="target_5d_return",
            model_name="random_forest",
            status="candidate",
            is_validated=True,
            validation_score=0.8,
            stale_after_days=1,
            retrain_type="test",
            metrics_summary={"validation_gate_version": VALIDATION_GATE_VERSION},
            notes="stale test",
            last_trained_at_utc="2020-01-01T00:00:00+00:00",
            last_evaluated_at_utc="2020-01-01T00:00:00+00:00",
            market="HK",
        )

        self.assertEqual(
            service.resolve_runtime_model_candidates(
                ticker="0700",
                market="HK",
                period="2y",
                periods=("2y",),
                target_name="target_5d_return",
            ),
            [],
        )

    def test_actual_model_and_fallback_reporting_are_truthful(self) -> None:
        self.assertEqual(
            _decision_model_reporting(
                model_loaded=True,
                selected_model_name="random_forest",
                selected_period="2y",
                selected_version="v2",
            ),
            ("random_forest", "2y", "v2", "ready"),
        )
        self.assertEqual(
            _decision_model_reporting(
                model_loaded=False,
                selected_model_name="auto_best",
                selected_period="2y",
                selected_version="selector",
                training_queued=True,
            ),
            ("backup_rules", "", "fallback", "training_pending"),
        )

    @patch("app.services.live_virtual_trader.get_model_lifecycle_service")
    @patch("app.services.live_virtual_trader._schedule_background_training_if_enabled")
    def test_current_unvalidated_hk_artifact_is_not_retrained_every_cycle(
        self,
        schedule_mock,
        lifecycle_factory,
    ) -> None:
        lifecycle_factory.return_value.list_registry.return_value = [
            {
                "model_name": "random_forest",
                "is_stale": False,
                "is_validated": False,
            }
        ]

        queued = ensure_active_ticker_model_training("0700", market="HK", period="2y")

        self.assertFalse(queued)
        schedule_mock.assert_not_called()

    @patch(
        "app.services.live_virtual_trader.list_compatible_saved_model_candidates",
        return_value=[{"ticker": "GLOBAL", "model_name": "ridge_regression"}],
    )
    @patch("app.services.live_virtual_trader.get_model_lifecycle_service")
    @patch("app.services.live_virtual_trader._schedule_background_training_if_enabled")
    def test_hk_global_artifact_does_not_block_exact_ticker_training(
        self,
        schedule_mock,
        lifecycle_factory,
        _saved_candidates_mock,
    ) -> None:
        lifecycle_factory.return_value.list_registry.return_value = []

        queued = ensure_active_ticker_model_training("1810", market="HK", period="2y")

        self.assertTrue(queued)
        schedule_mock.assert_called_once_with(
            ticker="1810",
            period="2y",
            benchmark="2800",
            market="HK",
        )

    @patch("app.services.live_virtual_trader.get_user_profile_store")
    def test_hk_live_universe_uses_all_persisted_active_tickers(
        self,
        profile_store_mock,
    ) -> None:
        active = ["0005", "0700", "1810", "3690", "9988"]
        profile_store_mock.return_value.get_effective_watchlist.return_value = (
            active,
            False,
            None,
        )

        self.assertEqual(_resolve_user_tickers("u1", None, market="HK"), active)
        profile_store_mock.return_value.get_effective_watchlist.assert_called_once_with(
            user_id="u1",
            market="HK",
        )

    @patch("app.services.user_profile_service.get_user_profile_store")
    @patch("app.services.model_lifecycle_service.train_pooled_baseline_models")
    @patch("app.services.model_lifecycle_service.train_baseline_models_for_ticker")
    def test_hk_lifecycle_trains_every_active_ticker_separately(
        self,
        train_mock,
        pooled_train_mock,
        profile_store_mock,
    ) -> None:
        active = ["0005", "0700", "1810", "3690", "9988"]
        profile_store_mock.return_value.list_effective_watchlist_tickers.return_value = active
        train_mock.return_value = [object()]
        pooled_train_mock.return_value = [object()]
        service = ModelLifecycleService(db_path=str(self.db_path))

        with patch.object(
            service,
            "register_training_result",
            return_value={"promoted": False},
        ):
            result = service.run_training_workflow(
                workflow_type="daily_incremental",
                trigger_reason="test:HK",
                market="HK",
            )

        self.assertEqual(result["processed_tickers"], 5)
        self.assertEqual(
            [call.kwargs["ticker"] for call in train_mock.call_args_list],
            active,
        )
        self.assertTrue(all(
            call.kwargs["market"] == "HK"
            and call.kwargs["benchmark"] == "2800"
            and call.kwargs["period"] == "2y"
            for call in train_mock.call_args_list
        ))
        pooled_train_mock.assert_called_once()
        self.assertEqual(pooled_train_mock.call_args.kwargs["market"], "HK")
        self.assertEqual(pooled_train_mock.call_args.kwargs["benchmark"], "2800")
        self.assertEqual(pooled_train_mock.call_args.kwargs["tickers"], active)

    def test_validated_hk_global_model_can_cover_an_exact_ticker(self) -> None:
        service = ModelLifecycleService(db_path=str(self.db_path))
        service._upsert_registry(
            ticker="GLOBAL",
            period="2y",
            target_name="target_5d_return",
            model_name="ridge_regression",
            status="production",
            is_validated=True,
            validation_score=0.68,
            stale_after_days=30,
            retrain_type="daily_incremental_pooled",
            metrics_summary={"validation_gate_version": VALIDATION_GATE_VERSION},
            notes="validated pooled HK model",
            last_trained_at_utc="2099-01-01T00:00:00+00:00",
            last_evaluated_at_utc="2099-01-01T00:00:00+00:00",
            last_promoted_at_utc="2099-01-01T00:00:00+00:00",
            market="HK",
        )

        candidates = service.resolve_runtime_model_candidates(
            ticker="1810",
            market="HK",
            period="2y",
            periods=("2y",),
            target_name="target_5d_return",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["ticker"], "GLOBAL")
        self.assertEqual(candidates[0]["source"], "shared_global_production")

    @patch("app.services.user_profile_service.get_user_profile_store")
    @patch("app.services.model_lifecycle_service.train_pooled_baseline_models")
    @patch("app.services.model_lifecycle_service.train_baseline_models_for_ticker")
    def test_hk_lifecycle_keeps_foundation_when_profile_has_one_ticker(
        self,
        train_mock,
        pooled_train_mock,
        profile_store_mock,
    ) -> None:
        profile_store_mock.return_value.list_effective_watchlist_tickers.return_value = ["0700"]
        train_mock.return_value = [object()]
        pooled_train_mock.return_value = [object()]
        service = ModelLifecycleService(db_path=str(self.db_path))

        with patch.object(
            service,
            "register_training_result",
            return_value={"validated": False, "promoted": False},
        ):
            result = service.run_training_workflow(
                workflow_type="daily_incremental",
                trigger_reason="test:HK:foundation",
                market="HK",
            )

        expected = ["0005", "0700", "1810", "3690", "9988"]
        self.assertEqual(result["processed_tickers"], len(expected))
        self.assertEqual(
            [call.kwargs["ticker"] for call in train_mock.call_args_list],
            expected,
        )
        self.assertEqual(pooled_train_mock.call_args.kwargs["tickers"], expected)

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
