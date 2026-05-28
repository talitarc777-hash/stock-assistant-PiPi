"""Tests for application settings normalization."""

from __future__ import annotations

import re
import unittest
from unittest.mock import patch

from app.core.settings import get_settings


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


if __name__ == "__main__":
    unittest.main()
