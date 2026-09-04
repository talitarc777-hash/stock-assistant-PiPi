"""Tests for the official HKEX metadata parser and persistent cache."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import io
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4
from xml.sax.saxutils import escape
import zipfile

from app.services.hkex_security_metadata import (
    HKEX_FULL_SECURITIES_URL,
    HkexSecurityMetadata,
    HkexSecurityMetadataService,
    parse_hkex_securities_xlsx,
)
from app.api.market_data import hk_security_metadata


def _fixture_workbook() -> bytes:
    rows = [
        ["List of Securities"],
        ["Updated as at 11/08/2026"],
        [
            "Stock Code",
            "Name of Securities",
            "Category",
            "Sub-Category",
            "Board Lot",
            "Expiry Date",
            "Admitted to CCASS",
            "Trading Currency",
        ],
        ["00005", "HSBC HOLDINGS", "Equity", "Equity Securities (Main Board)", "400", "", "Y", "HKD"],
        ["00700", "TENCENT", "Equity", "Equity Securities (Main Board)", "100", "", "Y", "HKD"],
        ["01810", "XIAOMI-W", "Equity", "Equity Securities (Main Board)", "200", "", "Y", "HKD"],
        ["03690", "MEITUAN-W", "Equity", "Equity Securities (Main Board)", "100", "", "Y", "HKD"],
        ["09988", "BABA-W", "Equity", "Equity Securities (Main Board)", "100", "", "Y", "HKD"],
    ]

    def column_name(index: int) -> str:
        result = ""
        value = index + 1
        while value:
            value, remainder = divmod(value - 1, 26)
            result = chr(65 + remainder) + result
        return result

    xml_rows = []
    for row_number, row in enumerate(rows, start=1):
        cells = []
        for column, value in enumerate(row):
            reference = f"{column_name(column)}{row_number}"
            cells.append(
                f'<c r="{reference}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            )
        xml_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    return output.getvalue()


class HkexSecurityMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        Path("data").mkdir(exist_ok=True)
        self.db_paths: list[Path] = []

    def tearDown(self) -> None:
        for path in self.db_paths:
            if path.exists():
                path.unlink()

    def _db_path(self) -> Path:
        path = Path(f"data/test_hkex_metadata_{uuid4().hex}.db")
        self.db_paths.append(path)
        return path

    def test_parser_normalizes_multiple_independent_board_lots(self) -> None:
        records, source_as_of = parse_hkex_securities_xlsx(_fixture_workbook())
        by_code = {record.stock_code: record for record in records}

        self.assertEqual(source_as_of, "2026-08-11")
        self.assertEqual(by_code["0005"].board_lot, 400)
        self.assertEqual(by_code["0700"].board_lot, 100)
        self.assertEqual(by_code["1810"].board_lot, 200)
        self.assertEqual(by_code["3690"].security_name, "MEITUAN-W")
        self.assertEqual(by_code["9988"].category, "Equity")
        self.assertTrue(by_code["1810"].ccass_admitted)
        self.assertEqual(by_code["1810"].source_url, HKEX_FULL_SECURITIES_URL)

    @patch("app.api.market_data.get_hk_security_metadata")
    def test_api_returns_cached_official_metadata(self, lookup) -> None:
        lookup.return_value = HkexSecurityMetadata(
            stock_code="1810",
            security_name="XIAOMI-W",
            board_lot=200,
            category="Equity",
            subcategory="Equity Securities (Main Board)",
            ccass_admitted=True,
            trading_currency="HKD",
            expiry_date=None,
            source_as_of="2026-08-11",
            source_url=HKEX_FULL_SECURITIES_URL,
        )

        response = hk_security_metadata("1810")

        self.assertEqual(response.stock_code, "1810")
        self.assertEqual(response.board_lot, 200)
        lookup.assert_called_once_with("1810")

    def test_cache_downloads_once_and_serves_arbitrary_normalized_symbols(self) -> None:
        calls = []

        def downloader() -> tuple[bytes, dict[str, str]]:
            calls.append(True)
            return _fixture_workbook(), {"etag": "test", "last_modified": "test"}

        service = HkexSecurityMetadataService(
            db_path=self._db_path(),
            refresh_interval=timedelta(hours=24),
            downloader=downloader,
            minimum_record_count=5,
        )
        self.assertEqual(service.get_security("5").board_lot, 400)
        self.assertEqual(service.get_security("0700.HK").board_lot, 100)
        self.assertEqual(service.get_security("1810").board_lot, 200)
        self.assertEqual(service.get_security("3690").board_lot, 100)
        self.assertEqual(service.get_security("9988").board_lot, 100)
        self.assertIsNone(service.get_security("1234"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(service.status()["record_count"], "5")

    def test_failed_refresh_preserves_and_serves_last_valid_cache(self) -> None:
        current_time = [datetime(2026, 8, 11, 8, 0, tzinfo=UTC)]
        should_fail = [False]
        calls = []

        def downloader() -> tuple[bytes, dict[str, str]]:
            calls.append(True)
            if should_fail[0]:
                raise RuntimeError("temporary HKEX outage")
            return _fixture_workbook(), {}

        service = HkexSecurityMetadataService(
            db_path=self._db_path(),
            refresh_interval=timedelta(hours=24),
            downloader=downloader,
            now_provider=lambda: current_time[0],
            minimum_record_count=5,
        )
        self.assertEqual(service.get_security("1810").board_lot, 200)

        current_time[0] += timedelta(days=2)
        should_fail[0] = True
        cached = service.get_security("1810")

        self.assertIsNotNone(cached)
        self.assertEqual(cached.board_lot, 200)
        self.assertFalse(service.status()["cache_fresh"])
        self.assertIn("temporary HKEX outage", service.status()["last_error"])
        self.assertEqual(service.get_security("0700").board_lot, 100)
        self.assertEqual(len(calls), 2)

    def test_localized_names_are_cached_per_ticker_and_stale_safe(self) -> None:
        current_time = [datetime(2026, 8, 11, 8, 0, tzinfo=UTC)]
        localized_calls: list[str] = []
        localized_values = {
            "0700": ("騰訊控股", "騰訊控股有限公司"),
            "1810": ("小米集團－Ｗ", "小米集團"),
        }

        def localized_provider(code: str) -> tuple[str | None, str | None]:
            localized_calls.append(code)
            value = localized_values.get(code)
            if value is None:
                raise RuntimeError("temporary localized-name outage")
            return value

        service = HkexSecurityMetadataService(
            db_path=self._db_path(),
            refresh_interval=timedelta(hours=24),
            downloader=lambda: (_fixture_workbook(), {}),
            localized_name_provider=localized_provider,
            now_provider=lambda: current_time[0],
            minimum_record_count=5,
        )

        self.assertEqual(service.get_localized_names("700")["security_name_zh"], "騰訊控股")
        self.assertEqual(service.get_localized_names("0700.HK")["issuer_name_zh"], "騰訊控股有限公司")
        self.assertEqual(service.get_localized_names("1810")["security_name_zh"], "小米集團－Ｗ")
        self.assertEqual(localized_calls, ["0700", "1810"])

        current_time[0] += timedelta(days=2)
        localized_values.pop("0700")
        stale = service.get_localized_names("0700")
        self.assertEqual(stale["security_name_zh"], "騰訊控股")
        self.assertEqual(localized_calls, ["0700", "1810", "0700"])


if __name__ == "__main__":
    unittest.main()
