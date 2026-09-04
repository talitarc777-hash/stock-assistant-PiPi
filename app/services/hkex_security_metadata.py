"""Official HKEX security metadata with a persistent, stale-safe cache.

HKEX publishes its Full List of Securities as an XLSX workbook.  This module
normalizes that workbook into a small SQLite table so trading requests never
need to download the file per ticker.  A refresh is attempted at most once per
configured interval; a failed download or parse leaves the last valid cache
untouched.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
import io
import json
import logging
from pathlib import Path
import re
import sqlite3
from threading import Lock
from typing import Callable
from urllib.parse import unquote
import xml.etree.ElementTree as ET
import zipfile

import requests

from app.core.settings import get_settings
from app.services.market_config import MarketValidationError, normalize_hk_ticker

logger = logging.getLogger(__name__)

HKEX_FULL_SECURITIES_URL = (
    "https://www.hkex.com.hk/eng/services/trading/securities/"
    "securitieslists/ListOfSecurities.xlsx"
)
HKEX_EQUITY_QUOTE_PAGE_URL = (
    "https://www.hkex.com.hk/Market-Data/Securities-Prices/Equities/"
    "Equities-Quote"
)
HKEX_EQUITY_QUOTE_API_URL = (
    "https://www1.hkex.com.hk/hkexwidget/data/getequityquote"
)
_XLSX_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_CELL_REFERENCE_PATTERN = re.compile(r"(?P<column>[A-Z]+)\d+")


class HkexMetadataError(RuntimeError):
    """Raised when official metadata cannot be downloaded or validated."""


@dataclass(frozen=True)
class HkexSecurityMetadata:
    stock_code: str
    security_name: str
    board_lot: int | None
    category: str | None
    subcategory: str | None
    ccass_admitted: bool | None
    trading_currency: str | None
    expiry_date: str | None
    source_as_of: str
    source_url: str
    security_name_zh: str | None = None
    issuer_name_zh: str | None = None
    localized_name_refreshed_at_utc: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _column_number(reference: str) -> int:
    match = _CELL_REFERENCE_PATTERN.fullmatch(reference)
    if match is None:
        raise HkexMetadataError(f"Invalid XLSX cell reference: {reference}")
    value = 0
    for character in match.group("column"):
        value = value * 26 + (ord(character) - ord("A") + 1)
    return value - 1


def _xlsx_rows(content: bytes) -> list[list[str]]:
    """Read the first worksheet using stdlib only (no new NanoPi dependency)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, OSError) as exc:
        raise HkexMetadataError("HKEX security list is not a valid XLSX file.") from exc

    with archive:
        names = set(archive.namelist())
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in names:
            raise HkexMetadataError("HKEX workbook does not contain its first worksheet.")

        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{{{_XLSX_MAIN_NS}}}si"):
                shared_strings.append(
                    "".join(
                        node.text or ""
                        for node in item.iter(f"{{{_XLSX_MAIN_NS}}}t")
                    )
                )

        root = ET.fromstring(archive.read(sheet_name))
        rows: list[list[str]] = []
        for row in root.findall(
            f".//{{{_XLSX_MAIN_NS}}}sheetData/{{{_XLSX_MAIN_NS}}}row"
        ):
            values: dict[int, str] = {}
            for cell in row.findall(f"{{{_XLSX_MAIN_NS}}}c"):
                reference = cell.get("r") or ""
                column = _column_number(reference)
                cell_type = cell.get("t")
                value_node = cell.find(f"{{{_XLSX_MAIN_NS}}}v")
                value = "" if value_node is None else str(value_node.text or "")
                if cell_type == "s" and value:
                    try:
                        value = shared_strings[int(value)]
                    except (IndexError, ValueError) as exc:
                        raise HkexMetadataError(
                            "HKEX workbook contains an invalid shared-string reference."
                        ) from exc
                elif cell_type == "inlineStr":
                    value = "".join(
                        node.text or ""
                        for node in cell.iter(f"{{{_XLSX_MAIN_NS}}}t")
                    )
                values[column] = value.strip()
            width = max(values, default=-1) + 1
            rows.append([values.get(index, "") for index in range(width)])
        return rows


def _parse_positive_integer(value: str) -> int | None:
    clean = str(value or "").replace(",", "").strip()
    if not clean:
        return None
    try:
        number = int(clean)
    except ValueError:
        return None
    return number if number > 0 else None


def _parse_yes_no(value: str) -> bool | None:
    clean = str(value or "").strip().upper()
    if clean == "Y":
        return True
    if clean == "N":
        return False
    return None


def parse_hkex_securities_xlsx(
    content: bytes,
    *,
    source_url: str = HKEX_FULL_SECURITIES_URL,
) -> tuple[list[HkexSecurityMetadata], str]:
    """Parse and normalize HKEX's official Full List of Securities workbook."""
    rows = _xlsx_rows(content)
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if "Stock Code" in row and "Board Lot" in row and "Name of Securities" in row
        ),
        None,
    )
    if header_index is None:
        raise HkexMetadataError("HKEX workbook headers are not recognized.")

    header = rows[header_index]
    columns = {name.strip(): index for index, name in enumerate(header) if name.strip()}

    def value(row: list[str], name: str) -> str:
        index = columns.get(name)
        return row[index].strip() if index is not None and index < len(row) else ""

    source_as_of = ""
    for row in rows[:header_index]:
        for cell in row:
            match = re.search(r"Updated as at\s+(\d{1,2})/(\d{1,2})/(\d{4})", cell, re.I)
            if match:
                day, month, year = (int(item) for item in match.groups())
                try:
                    source_as_of = datetime(year, month, day, tzinfo=UTC).date().isoformat()
                except ValueError as exc:
                    raise HkexMetadataError("HKEX workbook has an invalid update date.") from exc
                break
        if source_as_of:
            break
    if not source_as_of:
        raise HkexMetadataError("HKEX workbook update date is missing.")

    records: dict[str, HkexSecurityMetadata] = {}
    for row in rows[header_index + 1 :]:
        raw_code = value(row, "Stock Code")
        if not raw_code.isdigit():
            continue
        numeric_code = int(raw_code)
        # The app currently supports the ordinary 0001-9999 HK symbol space.
        # Five-digit structured products and alternate counters remain outside
        # its accepted ticker grammar and are intentionally not coerced.
        if numeric_code <= 0 or numeric_code > 9999:
            continue
        code = f"{numeric_code:04d}"
        name = value(row, "Name of Securities")
        if not name:
            continue
        records[code] = HkexSecurityMetadata(
            stock_code=code,
            security_name=name,
            board_lot=_parse_positive_integer(value(row, "Board Lot")),
            category=value(row, "Category") or None,
            subcategory=value(row, "Sub-Category") or None,
            ccass_admitted=_parse_yes_no(value(row, "Admitted to CCASS")),
            trading_currency=value(row, "Trading Currency") or None,
            expiry_date=value(row, "Expiry Date") or None,
            source_as_of=source_as_of,
            source_url=source_url,
        )
    return list(records.values()), source_as_of


class HkexSecurityMetadataService:
    """Look up HK security metadata through a daily persistent cache."""

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        refresh_interval: timedelta | None = None,
        timeout_seconds: float = 15.0,
        downloader: Callable[[], tuple[bytes, dict[str, str]]] | None = None,
        localized_name_provider: Callable[[str], tuple[str | None, str | None]] | None = None,
        now_provider: Callable[[], datetime] | None = None,
        minimum_record_count: int = 500,
        failure_retry_interval: timedelta = timedelta(hours=1),
    ) -> None:
        settings = get_settings()
        self.db_path = Path(db_path or settings.hkex_metadata_db_path)
        self.refresh_interval = refresh_interval or timedelta(
            hours=settings.hkex_metadata_refresh_hours
        )
        self.timeout_seconds = timeout_seconds
        self.downloader = downloader or self._download_official_workbook
        self.localized_name_provider = localized_name_provider or self._download_localized_name
        self.now_provider = now_provider or (lambda: datetime.now(UTC))
        self.minimum_record_count = minimum_record_count
        self.failure_retry_interval = failure_retry_interval
        self._refresh_lock = Lock()
        self._localized_name_lock = Lock()
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hkex_securities (
                    stock_code TEXT PRIMARY KEY,
                    security_name TEXT NOT NULL,
                    board_lot INTEGER,
                    category TEXT,
                    subcategory TEXT,
                    ccass_admitted INTEGER,
                    trading_currency TEXT,
                    expiry_date TEXT,
                    source_as_of TEXT NOT NULL,
                    source_url TEXT NOT NULL
                )
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(hkex_securities)")
            }
            for name in (
                "security_name_zh",
                "issuer_name_zh",
                "localized_name_refreshed_at_utc",
                "localized_name_last_attempt_at_utc",
            ):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE hkex_securities ADD COLUMN {name} TEXT"
                    )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hkex_metadata_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def _download_localized_name(self, stock_code: str) -> tuple[str | None, str | None]:
        """Fetch official Traditional Chinese short/full names from HKEX."""
        symbol = str(int(normalize_hk_ticker(stock_code)))
        page_url = f"{HKEX_EQUITY_QUOTE_PAGE_URL}?sym={symbol}&sc_lang=zh-HK"
        headers = {
            "User-Agent": "Mozilla/5.0 StockAssistantPiPi/1.0",
            "Accept-Language": "zh-HK,zh-TW;q=0.9,en;q=0.7",
        }
        with requests.Session() as session:
            page = session.get(page_url, timeout=self.timeout_seconds, headers=headers)
            page.raise_for_status()
            token_match = re.search(r'return\s+"(evLts[^"]+)"', page.text)
            if token_match is None:
                raise HkexMetadataError("HKEX quote page did not provide an API token.")
            timestamp = int(self.now_provider().timestamp() * 1000)
            callback = f"stockAssistant{timestamp}"
            response = session.get(
                HKEX_EQUITY_QUOTE_API_URL,
                timeout=self.timeout_seconds,
                headers={**headers, "Referer": page_url, "Accept": "*/*"},
                params={
                    "sym": symbol,
                    "token": unquote(token_match.group(1)),
                    "lang": "chi",
                    "qid": str(timestamp),
                    "callback": callback,
                    "_": str(timestamp),
                },
            )
            response.raise_for_status()
        body = response.text.strip()
        start = body.find("(")
        end = body.rfind(")")
        if start < 0 or end <= start:
            raise HkexMetadataError("HKEX quote API returned an invalid JSONP response.")
        try:
            payload = json.loads(body[start + 1 : end])
            quote = payload["data"]["quote"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise HkexMetadataError("HKEX quote API response is missing quote data.") from exc
        response_code = str(payload.get("data", {}).get("responsecode", ""))
        if response_code != "000" or str(quote.get("sym", "")) != symbol:
            raise HkexMetadataError("HKEX quote API returned a mismatched security.")
        short_name = str(quote.get("nm_s") or "").strip() or None
        issuer_name = str(quote.get("nm") or quote.get("issuer_name") or "").strip() or None
        if not short_name and not issuer_name:
            raise HkexMetadataError("HKEX quote API returned no localized security name.")
        return short_name, issuer_name

    def _download_official_workbook(self) -> tuple[bytes, dict[str, str]]:
        response = requests.get(
            HKEX_FULL_SECURITIES_URL,
            timeout=self.timeout_seconds,
            headers={
                "User-Agent": "StockAssistantPiPi/1.0 HKEX-metadata-cache",
                "Accept": (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
            },
        )
        response.raise_for_status()
        if len(response.content) > 15 * 1024 * 1024:
            raise HkexMetadataError("HKEX security list exceeds the safe download limit.")
        return response.content, {
            "etag": response.headers.get("ETag", ""),
            "last_modified": response.headers.get("Last-Modified", ""),
        }

    def _state(self) -> dict[str, str]:
        with closing(self._connect()) as connection:
            return {
                str(row["key"]): str(row["value"])
                for row in connection.execute("SELECT key, value FROM hkex_metadata_state")
            }

    def _cache_is_fresh(self) -> bool:
        refreshed_at = self._state().get("refreshed_at_utc")
        if not refreshed_at:
            return False
        try:
            refreshed = datetime.fromisoformat(refreshed_at)
            if refreshed.tzinfo is None:
                refreshed = refreshed.replace(tzinfo=UTC)
        except ValueError:
            return False
        return self.now_provider() - refreshed < self.refresh_interval

    def _refresh_attempt_allowed(self) -> bool:
        last_attempt = self._state().get("last_attempt_at_utc")
        if not last_attempt:
            return True
        try:
            attempted = datetime.fromisoformat(last_attempt)
            if attempted.tzinfo is None:
                attempted = attempted.replace(tzinfo=UTC)
        except ValueError:
            return True
        return self.now_provider() - attempted >= self.failure_retry_interval

    def _record_failed_attempt(self, exc: Exception) -> None:
        attempted_at = self.now_provider().astimezone(UTC).replace(microsecond=0).isoformat()
        try:
            with closing(self._connect()) as connection:
                connection.executemany(
                    """
                    INSERT INTO hkex_metadata_state (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    [
                        ("last_attempt_at_utc", attempted_at),
                        ("last_error", str(exc)[:500]),
                    ],
                )
                connection.commit()
        except sqlite3.Error:
            logger.exception("Could not record failed HKEX metadata refresh attempt")

    def refresh_if_stale(self) -> bool:
        if self._cache_is_fresh():
            return False
        if not self._refresh_attempt_allowed():
            return False
        with self._refresh_lock:
            if self._cache_is_fresh():
                return False
            if not self._refresh_attempt_allowed():
                return False
            try:
                self.refresh()
                return True
            except Exception as exc:
                # The replacement transaction is never entered until the new
                # workbook has been fully parsed and validated.
                logger.warning(
                    "HKEX metadata refresh failed; retaining last valid cache at %s: %s",
                    self.db_path,
                    exc,
                )
                self._record_failed_attempt(exc)
                return False

    def refresh(self) -> dict[str, object]:
        content, response_metadata = self.downloader()
        records, source_as_of = parse_hkex_securities_xlsx(content)
        board_lot_count = sum(record.board_lot is not None for record in records)
        if len(records) < self.minimum_record_count:
            raise HkexMetadataError(
                f"HKEX refresh yielded only {len(records)} securities; cache was not replaced."
            )
        if board_lot_count < max(1, self.minimum_record_count // 2):
            raise HkexMetadataError(
                "HKEX refresh contains too few reliable board-lot values; cache was not replaced."
            )

        refreshed_at = self.now_provider().astimezone(UTC).replace(microsecond=0).isoformat()
        state = {
            "refreshed_at_utc": refreshed_at,
            "last_attempt_at_utc": refreshed_at,
            "last_error": "",
            "source_as_of": source_as_of,
            "source_url": HKEX_FULL_SECURITIES_URL,
            "record_count": str(len(records)),
            "board_lot_count": str(board_lot_count),
            "etag": response_metadata.get("etag", ""),
            "last_modified": response_metadata.get("last_modified", ""),
        }
        with closing(self._connect()) as connection:
            localized_names = {
                str(row["stock_code"]): (
                    row["security_name_zh"],
                    row["issuer_name_zh"],
                    row["localized_name_refreshed_at_utc"],
                    row["localized_name_last_attempt_at_utc"],
                )
                for row in connection.execute(
                    """
                    SELECT stock_code, security_name_zh, issuer_name_zh,
                           localized_name_refreshed_at_utc,
                           localized_name_last_attempt_at_utc
                    FROM hkex_securities
                    WHERE security_name_zh IS NOT NULL OR issuer_name_zh IS NOT NULL
                    """
                )
            }
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM hkex_securities")
            connection.executemany(
                """
                INSERT INTO hkex_securities (
                    stock_code, security_name, board_lot, category, subcategory,
                    ccass_admitted, trading_currency, expiry_date, source_as_of,
                    source_url, security_name_zh, issuer_name_zh,
                    localized_name_refreshed_at_utc,
                    localized_name_last_attempt_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        record.stock_code,
                        record.security_name,
                        record.board_lot,
                        record.category,
                        record.subcategory,
                        None if record.ccass_admitted is None else int(record.ccass_admitted),
                        record.trading_currency,
                        record.expiry_date,
                        record.source_as_of,
                        record.source_url,
                        *localized_names.get(record.stock_code, (None, None, None, None)),
                    )
                    for record in records
                ],
            )
            connection.execute("DELETE FROM hkex_metadata_state")
            connection.executemany(
                "INSERT INTO hkex_metadata_state (key, value) VALUES (?, ?)",
                list(state.items()),
            )
            connection.commit()
        logger.info(
            "HKEX metadata cache refreshed source_as_of=%s records=%d board_lots=%d path=%s",
            source_as_of,
            len(records),
            board_lot_count,
            self.db_path,
        )
        return {**state, "record_count": len(records), "board_lot_count": board_lot_count}

    @staticmethod
    def _timestamp_is_fresh(value: object, now: datetime, interval: timedelta) -> bool:
        if not value:
            return False
        try:
            timestamp = datetime.fromisoformat(str(value))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
        except ValueError:
            return False
        return now - timestamp < interval

    def get_localized_names(self, ticker: str) -> dict[str, str | None] | None:
        """Return cached HKEX Traditional Chinese names, refreshing conservatively."""
        code = normalize_hk_ticker(ticker)
        self.refresh_if_stale()

        def load_row() -> sqlite3.Row | None:
            with closing(self._connect()) as connection:
                return connection.execute(
                    "SELECT * FROM hkex_securities WHERE stock_code = ?",
                    (code,),
                ).fetchone()

        row = load_row()
        if row is None:
            return None
        now = self.now_provider().astimezone(UTC)
        has_cached_name = bool(row["security_name_zh"] or row["issuer_name_zh"])
        if has_cached_name and self._timestamp_is_fresh(
            row["localized_name_refreshed_at_utc"], now, self.refresh_interval
        ):
            return {
                "security_name_zh": str(row["security_name_zh"]) if row["security_name_zh"] else None,
                "issuer_name_zh": str(row["issuer_name_zh"]) if row["issuer_name_zh"] else None,
            }
        if self._timestamp_is_fresh(
            row["localized_name_last_attempt_at_utc"], now, self.failure_retry_interval
        ):
            return {
                "security_name_zh": str(row["security_name_zh"]) if row["security_name_zh"] else None,
                "issuer_name_zh": str(row["issuer_name_zh"]) if row["issuer_name_zh"] else None,
            } if has_cached_name else None

        with self._localized_name_lock:
            row = load_row()
            if row is None:
                return None
            now = self.now_provider().astimezone(UTC)
            if self._timestamp_is_fresh(
                row["localized_name_refreshed_at_utc"], now, self.refresh_interval
            ):
                return {
                    "security_name_zh": str(row["security_name_zh"]) if row["security_name_zh"] else None,
                    "issuer_name_zh": str(row["issuer_name_zh"]) if row["issuer_name_zh"] else None,
                }
            attempted_at = now.replace(microsecond=0).isoformat()
            try:
                short_name, issuer_name = self.localized_name_provider(code)
                with closing(self._connect()) as connection:
                    connection.execute(
                        """
                        UPDATE hkex_securities
                        SET security_name_zh = ?, issuer_name_zh = ?,
                            localized_name_refreshed_at_utc = ?,
                            localized_name_last_attempt_at_utc = ?
                        WHERE stock_code = ?
                        """,
                        (short_name, issuer_name, attempted_at, attempted_at, code),
                    )
                    connection.commit()
                return {
                    "security_name_zh": short_name,
                    "issuer_name_zh": issuer_name,
                }
            except Exception as exc:
                logger.warning(
                    "HKEX localized name refresh failed for %s; retaining cached name: %s",
                    code,
                    exc,
                )
                with closing(self._connect()) as connection:
                    connection.execute(
                        """
                        UPDATE hkex_securities
                        SET localized_name_last_attempt_at_utc = ?
                        WHERE stock_code = ?
                        """,
                        (attempted_at, code),
                    )
                    connection.commit()
                return {
                    "security_name_zh": str(row["security_name_zh"]) if row["security_name_zh"] else None,
                    "issuer_name_zh": str(row["issuer_name_zh"]) if row["issuer_name_zh"] else None,
                } if has_cached_name else None

    def get_security(
        self,
        ticker: str,
        *,
        refresh_if_stale: bool = True,
    ) -> HkexSecurityMetadata | None:
        code = normalize_hk_ticker(ticker)
        if refresh_if_stale:
            self.refresh_if_stale()
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM hkex_securities WHERE stock_code = ?",
                (code,),
            ).fetchone()
        if row is None:
            return None
        ccass = row["ccass_admitted"]
        return HkexSecurityMetadata(
            stock_code=str(row["stock_code"]),
            security_name=str(row["security_name"]),
            board_lot=int(row["board_lot"]) if row["board_lot"] is not None else None,
            category=str(row["category"]) if row["category"] else None,
            subcategory=str(row["subcategory"]) if row["subcategory"] else None,
            ccass_admitted=bool(ccass) if ccass is not None else None,
            trading_currency=(
                str(row["trading_currency"]) if row["trading_currency"] else None
            ),
            expiry_date=str(row["expiry_date"]) if row["expiry_date"] else None,
            source_as_of=str(row["source_as_of"]),
            source_url=str(row["source_url"]),
            security_name_zh=(
                str(row["security_name_zh"]) if row["security_name_zh"] else None
            ),
            issuer_name_zh=(
                str(row["issuer_name_zh"]) if row["issuer_name_zh"] else None
            ),
            localized_name_refreshed_at_utc=(
                str(row["localized_name_refreshed_at_utc"])
                if row["localized_name_refreshed_at_utc"] else None
            ),
        )

    def status(self) -> dict[str, object]:
        state = self._state()
        return {
            "cache_path": str(self.db_path),
            "cache_fresh": self._cache_is_fresh(),
            "refresh_interval_hours": self.refresh_interval.total_seconds() / 3600,
            **state,
        }


@lru_cache(maxsize=1)
def get_hkex_metadata_service() -> HkexSecurityMetadataService:
    return HkexSecurityMetadataService()


def get_hk_security_metadata(ticker: str) -> HkexSecurityMetadata | None:
    return get_hkex_metadata_service().get_security(ticker)


def get_hk_security_localized_names(ticker: str) -> dict[str, str | None] | None:
    return get_hkex_metadata_service().get_localized_names(ticker)


def get_hk_board_lot(ticker: str) -> int | None:
    try:
        metadata = get_hk_security_metadata(ticker)
    except MarketValidationError:
        raise
    return metadata.board_lot if metadata is not None else None
