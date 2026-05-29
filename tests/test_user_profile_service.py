"""Tests for the shared SQLite-backed user profile store."""

from __future__ import annotations

import unittest
import sqlite3
import tempfile
import os
from pathlib import Path
from uuid import uuid4

from app.models.user_profile import (
    UserAlertSettingsUpdateRequest,
    UserProfileResetRequest,
    UserProfileSettingsUpdateRequest,
)
from app.services.user_profile_service import UserProfileStore


class UserProfileStoreTests(unittest.TestCase):
    """Verify shared profile/watchlist/alert persistence and defaults."""

    def setUp(self) -> None:
        temp_root = Path(os.getenv("TEST_TEMP_DIR", r"C:\tmp"))
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(dir=temp_root)
        self.test_dir = Path(self.temp_dir.name)
        self.db_path = self.test_dir / f"user_profiles_{uuid4().hex}.db"
        self.store = UserProfileStore(db_path=str(self.db_path))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_get_or_create_profile_uses_defaults(self) -> None:
        profile = self.store.get_or_create_profile("demo-user")

        self.assertEqual(profile.user_id, "demo-user")
        self.assertEqual(profile.preferred_language, "bilingual")
        self.assertEqual(profile.selected_evaluation_model, "logistic_regression")
        self.assertEqual(profile.default_watchlist, [])
        self.assertTrue(profile.alert_enabled)

    def test_effective_watchlist_falls_back_to_system_default(self) -> None:
        watchlist, using_system_default, _ = self.store.get_effective_watchlist("demo-user")

        self.assertTrue(using_system_default)
        self.assertEqual(watchlist, self.store.default_watchlist)

    def test_watchlist_updates_normalize_special_tickers(self) -> None:
        profile = self.store.update_profile_settings(
            UserProfileSettingsUpdateRequest(
                user_id="demo-user",
                selected_evaluation_model="random_forest",
                default_watchlist=["BRK.B", "tsla"],
                last_active_source="dashboard",
            )
        )

        self.assertEqual(profile.selected_evaluation_model, "random_forest")
        self.assertEqual(profile.default_watchlist, ["BRK-B", "TSLA"])

    def test_watchlist_keeps_exchange_dot_suffix(self) -> None:
        profile = self.store.update_profile_settings(
            UserProfileSettingsUpdateRequest(
                user_id="demo-user",
                default_watchlist=["0700.HK", "9988.hk"],
                last_active_source="dashboard",
            )
        )

        self.assertEqual(profile.default_watchlist, ["0700.HK", "9988.HK"])

    def test_watchlist_converts_legacy_dash_exchange_suffix(self) -> None:
        profile = self.store.update_profile_settings(
            UserProfileSettingsUpdateRequest(
                user_id="demo-user",
                default_watchlist=["0700-HK", "600519-SS"],
                last_active_source="dashboard",
            )
        )

        self.assertEqual(profile.default_watchlist, ["0700.HK", "600519.SS"])

    def test_alert_settings_update_persists(self) -> None:
        profile = self.store.update_alert_settings(
            UserAlertSettingsUpdateRequest(
                user_id="demo-user",
                alert_enabled=False,
                alert_threshold_high=85,
                alert_threshold_low=40,
                alert_watchlist=["AAPL", "MSFT"],
                preferred_delivery_source="discord",
                last_active_source="discord",
            )
        )

        self.assertFalse(profile.alert_enabled)
        self.assertEqual(profile.alert_threshold_high, 85)
        self.assertEqual(profile.alert_threshold_low, 40)
        self.assertEqual(profile.alert_watchlist, ["AAPL", "MSFT"])

    def test_reset_profile_restores_shared_defaults(self) -> None:
        self.store.update_profile_settings(
            UserProfileSettingsUpdateRequest(
                user_id="demo-user",
                preferred_language="zh",
                compact_mode=True,
                default_watchlist=["TSLA"],
                last_active_source="dashboard",
            )
        )
        self.store.update_alert_settings(
            UserAlertSettingsUpdateRequest(
                user_id="demo-user",
                alert_enabled=False,
                alert_threshold_high=90,
                alert_threshold_low=30,
                alert_watchlist=["NVDA"],
            )
        )

        profile = self.store.reset_profile(
            UserProfileResetRequest(user_id="demo-user", last_active_source="discord")
        )

        self.assertEqual(profile.preferred_language, "bilingual")
        self.assertFalse(profile.compact_mode)
        self.assertEqual(profile.default_watchlist, [])
        self.assertTrue(profile.alert_enabled)
        self.assertEqual(profile.alert_threshold_high, 80)
        self.assertEqual(profile.alert_threshold_low, 45)
        self.assertEqual(profile.alert_watchlist, [])
        self.assertEqual(profile.last_active_source, "discord")

    def test_list_alert_enabled_user_summaries_uses_fallback_watchlist(self) -> None:
        self.store.update_profile_settings(
            UserProfileSettingsUpdateRequest(
                user_id="demo-user",
                default_watchlist=["AAPL", "MSFT"],
            )
        )
        summaries = self.store.list_alert_enabled_user_summaries()

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].user_id, "demo-user")
        self.assertEqual(summaries[0].alert_watchlist, ["AAPL", "MSFT"])

    def test_legacy_user_profile_table_is_migrated(self) -> None:
        legacy_db = self.test_dir / f"legacy_user_profiles_{uuid4().hex}.db"
        with sqlite3.connect(legacy_db) as connection:
            connection.execute(
                """
                CREATE TABLE user_profiles (
                    user_id TEXT PRIMARY KEY,
                    default_watchlist TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO user_profiles (user_id, default_watchlist) VALUES (?, ?)",
                ("legacy-user", '["VOO"]'),
            )
            connection.commit()

        try:
            legacy_store = UserProfileStore(db_path=str(legacy_db))
            profile = legacy_store.get_or_create_profile("legacy-user")
            watchlist, using_default, _ = legacy_store.get_effective_watchlist("legacy-user")
        finally:
            if legacy_db.exists():
                try:
                    legacy_db.unlink()
                except PermissionError:
                    pass

        self.assertEqual(profile.selected_evaluation_model, "logistic_regression")
        self.assertEqual(profile.preferred_language, "bilingual")
        self.assertEqual(watchlist, ["VOO"])
        self.assertFalse(using_default)


if __name__ == "__main__":
    unittest.main()
