"""Tests for application settings normalization."""

from __future__ import annotations

import re
import os
import shutil
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.core.settings import _resolve_profile_db_path, get_settings


class SettingsTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_cors_regex_allows_cloudflare_pages_preview_domain(self) -> None:
        get_settings.cache_clear()
        with patch.dict("os.environ", {"CORS_ALLOW_ORIGIN_REGEX": r"^https://example\.com$"}):
            settings = get_settings()

        self.assertIsNotNone(settings.cors_allow_origin_regex)
        self.assertTrue(
            re.match(
                settings.cors_allow_origin_regex or "",
                "https://1b001fa6.stock-assistant-pipi.pages.dev",
            )
        )
        self.assertTrue(re.match(settings.cors_allow_origin_regex or "", "https://example.com"))

    def test_default_cors_origins_include_tailscale_hosts(self) -> None:
        get_settings.cache_clear()
        with patch.dict("os.environ", {}, clear=True):
            settings = get_settings()

        self.assertIn("https://cowbox.dpdns.org", settings.cors_allow_origins)
        self.assertIn("https://tail8919df.ts.net", settings.cors_allow_origins)

    def test_production_relative_database_is_migrated_outside_checkout(self) -> None:
        temp_root = Path("data") / "test_settings"
        temp_root.mkdir(parents=True, exist_ok=True)
        root = temp_root / uuid4().hex
        root.mkdir()
        try:
            legacy = root / "checkout" / "data" / "user_profiles.db"
            persistent = root / "persistent"
            legacy.parent.mkdir(parents=True)
            with sqlite3.connect(legacy) as connection:
                connection.execute("CREATE TABLE preserved (value TEXT NOT NULL)")
                connection.execute("INSERT INTO preserved (value) VALUES ('existing-account-data')")

            with patch.dict(
                "os.environ",
                {"PERSISTENT_DATA_DIR": str(persistent)},
            ):
                with patch("pathlib.Path.cwd", return_value=root / "checkout"):
                    relative_legacy = legacy.relative_to(root / "checkout")
                    target = _resolve_profile_db_path(
                        app_env="production",
                        configured_path=str(relative_legacy),
                    )

            self.assertEqual(Path(target), persistent / "user_profiles.db")
            with sqlite3.connect(target) as connection:
                row = connection.execute("SELECT value FROM preserved").fetchone()
            self.assertEqual(row, ("existing-account-data",))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_development_database_path_is_unchanged(self) -> None:
        self.assertEqual(
            _resolve_profile_db_path("development", "data/user_profiles.db"),
            str(Path("data/user_profiles.db")),
        )


if __name__ == "__main__":
    unittest.main()
