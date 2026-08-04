"""Deterministic ticker classification shared by API response models.

The classifier deliberately prefers explicit market/provider metadata and local
universe metadata. Symbol inference is conservative so an unfamiliar symbol is
reported as unknown instead of silently being called a stock.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Any


PRIMARY_TICKER_CLASSES = {
    "stock",
    "etf",
    "index",
    "reit",
    "fixed_income",
    "commodity",
    "forex",
    "crypto",
    "derivative",
    "cash",
    "unknown",
}

STOCK_SUBCLASSES = {
    "technology",
    "financials",
    "consumer_cyclical",
    "consumer_defensive",
    "healthcare",
    "industrials",
    "energy",
    "materials",
    "utilities",
    "real_estate",
    "communication_services",
    "other",
    "unknown",
}

SECTOR_TO_STOCK_SUBCLASS = {
    "technology": "technology",
    "financial services": "financials",
    "financials": "financials",
    "consumer cyclical": "consumer_cyclical",
    "consumer defensive": "consumer_defensive",
    "healthcare": "healthcare",
    "industrials": "industrials",
    "energy": "energy",
    "basic materials": "materials",
    "materials": "materials",
    "utilities": "utilities",
    "real estate": "real_estate",
    "communication services": "communication_services",
}

QUOTE_TYPE_TO_PRIMARY_CLASS = {
    "EQUITY": "stock",
    "ETF": "etf",
    "INDEX": "index",
    "CURRENCY": "forex",
    "CRYPTOCURRENCY": "crypto",
    "FUTURE": "derivative",
    "OPTION": "derivative",
}

# Explicit exceptions and high-traffic symbols. The local universe file below
# supplies the broader stock/ETF primary-class metadata.
TICKER_CLASSIFICATION_OVERRIDES: dict[str, dict[str, str]] = {
    "AAPL": {"primary_class": "stock", "stock_subclass": "technology"},
    "MSFT": {"primary_class": "stock", "stock_subclass": "technology"},
    "NVDA": {"primary_class": "stock", "stock_subclass": "technology"},
    "AMD": {"primary_class": "stock", "stock_subclass": "technology"},
    "AVGO": {"primary_class": "stock", "stock_subclass": "technology"},
    "ORCL": {"primary_class": "stock", "stock_subclass": "technology"},
    "CRM": {"primary_class": "stock", "stock_subclass": "technology"},
    "ADBE": {"primary_class": "stock", "stock_subclass": "technology"},
    "CSCO": {"primary_class": "stock", "stock_subclass": "technology"},
    "ACN": {"primary_class": "stock", "stock_subclass": "technology"},
    "INTC": {"primary_class": "stock", "stock_subclass": "technology"},
    "TXN": {"primary_class": "stock", "stock_subclass": "technology"},
    "QCOM": {"primary_class": "stock", "stock_subclass": "technology"},
    "AMZN": {"primary_class": "stock", "stock_subclass": "consumer_cyclical"},
    "TSLA": {"primary_class": "stock", "stock_subclass": "consumer_cyclical"},
    "HD": {"primary_class": "stock", "stock_subclass": "consumer_cyclical"},
    "MCD": {"primary_class": "stock", "stock_subclass": "consumer_cyclical"},
    "GOOG": {"primary_class": "stock", "stock_subclass": "communication_services"},
    "GOOGL": {"primary_class": "stock", "stock_subclass": "communication_services"},
    "META": {"primary_class": "stock", "stock_subclass": "communication_services"},
    "NFLX": {"primary_class": "stock", "stock_subclass": "communication_services"},
    "DIS": {"primary_class": "stock", "stock_subclass": "communication_services"},
    "CMCSA": {"primary_class": "stock", "stock_subclass": "communication_services"},
    "JPM": {"primary_class": "stock", "stock_subclass": "financials"},
    "V": {"primary_class": "stock", "stock_subclass": "financials"},
    "MA": {"primary_class": "stock", "stock_subclass": "financials"},
    "BRK-B": {"primary_class": "stock", "stock_subclass": "financials"},
    "BAC": {"primary_class": "stock", "stock_subclass": "financials"},
    "WFC": {"primary_class": "stock", "stock_subclass": "financials"},
    "UNH": {"primary_class": "stock", "stock_subclass": "healthcare"},
    "LLY": {"primary_class": "stock", "stock_subclass": "healthcare"},
    "MRK": {"primary_class": "stock", "stock_subclass": "healthcare"},
    "JNJ": {"primary_class": "stock", "stock_subclass": "healthcare"},
    "ABBV": {"primary_class": "stock", "stock_subclass": "healthcare"},
    "TMO": {"primary_class": "stock", "stock_subclass": "healthcare"},
    "ABT": {"primary_class": "stock", "stock_subclass": "healthcare"},
    "XOM": {"primary_class": "stock", "stock_subclass": "energy"},
    "CVX": {"primary_class": "stock", "stock_subclass": "energy"},
    "PG": {"primary_class": "stock", "stock_subclass": "consumer_defensive"},
    "WMT": {"primary_class": "stock", "stock_subclass": "consumer_defensive"},
    "COST": {"primary_class": "stock", "stock_subclass": "consumer_defensive"},
    "KO": {"primary_class": "stock", "stock_subclass": "consumer_defensive"},
    "PEP": {"primary_class": "stock", "stock_subclass": "consumer_defensive"},
    "PM": {"primary_class": "stock", "stock_subclass": "consumer_defensive"},
    "CAT": {"primary_class": "stock", "stock_subclass": "industrials"},
    "HON": {"primary_class": "stock", "stock_subclass": "industrials"},
    "LIN": {"primary_class": "stock", "stock_subclass": "materials"},
    "NEE": {"primary_class": "stock", "stock_subclass": "utilities"},
    "0700.HK": {"primary_class": "stock", "stock_subclass": "communication_services"},
    "^GSPC": {"primary_class": "index"},
    "^DJI": {"primary_class": "index"},
    "^IXIC": {"primary_class": "index"},
    "BTC-USD": {"primary_class": "crypto"},
    "ETH-USD": {"primary_class": "crypto"},
    "XAUUSD=X": {"primary_class": "commodity"},
    "US10Y": {"primary_class": "fixed_income"},
    "USD": {"primary_class": "cash"},
    "HKD": {"primary_class": "cash"},
    "CASH": {"primary_class": "cash"},
}

LOCAL_REIT_TICKERS = {
    "AMT", "AVB", "CCI", "CPT", "DLR", "EQR", "ESS", "EXR", "IRM",
    "MAA", "PLD", "PSA", "UDR", "WELL",
}

_CRYPTO_BASES = {"BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "BNB", "AVAX", "DOT", "LTC"}
_FOREX_CODES = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "HKD", "CNY", "CNH", "SGD"}


@dataclass(frozen=True)
class TickerClassification:
    ticker: str
    primary_ticker_class: str
    stock_subclass: str | None
    classification_source: str


def normalize_ticker_symbol(value: Any) -> str:
    """Normalize common Yahoo-style, HK, forex, crypto and class-share symbols."""
    symbol = str(value or "").strip().upper().replace(" ", "")
    if not symbol:
        return ""

    hk_match = re.fullmatch(r"(\d{1,4})\.HK", symbol)
    if hk_match:
        return f"{hk_match.group(1).zfill(4)}.HK"

    pair_match = re.fullmatch(r"([A-Z]{3,10})/([A-Z]{3,4})", symbol)
    if pair_match:
        base, quote = pair_match.groups()
        if base in _CRYPTO_BASES:
            return f"{base}-{quote}"
        if base in _FOREX_CODES and quote in _FOREX_CODES:
            return f"{base}{quote}=X"

    if re.fullmatch(r"[A-Z]{1,5}\.[A-Z]", symbol):
        return symbol.replace(".", "-")
    return symbol


def _clean_value(value: Any) -> str:
    return str(value or "").strip()


def _normalized_config_value(value: Any, supported: set[str]) -> str | None:
    normalized = _clean_value(value).lower().replace("-", "_").replace(" ", "_")
    return normalized if normalized in supported else None


@lru_cache(maxsize=1)
def _local_universe_classes() -> dict[str, str]:
    """Read the repository''s explicitly grouped ETF/stock universe metadata."""
    path = Path(__file__).resolve().parents[2] / "config" / "universe_tickers.txt"
    if not path.exists():
        return {}

    current_class: str | None = None
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("# Broad market ETFs"):
            current_class = "etf"
            continue
        if line.startswith("# Mega caps / large caps"):
            current_class = "stock"
            continue
        if not line or line.startswith("#") or current_class is None:
            continue
        symbol = normalize_ticker_symbol(line)
        if symbol:
            result[symbol] = "reit" if symbol in LOCAL_REIT_TICKERS else current_class
    return result


def _provider_primary(metadata: dict[str, Any]) -> str | None:
    explicit = _normalized_config_value(
        metadata.get("primary_ticker_class") or metadata.get("primaryClass"),
        PRIMARY_TICKER_CLASSES,
    )
    if explicit and explicit != "unknown":
        return explicit

    quote_type = _clean_value(metadata.get("quote_type") or metadata.get("quoteType")).upper()
    mapped = QUOTE_TYPE_TO_PRIMARY_CLASS.get(quote_type)
    industry = _clean_value(metadata.get("industry")).lower()
    if mapped == "stock" and "reit" in industry:
        return "reit"
    if mapped:
        return mapped
    if metadata.get("sector"):
        return "stock"
    return None


def _stock_subclass_from_metadata(metadata: dict[str, Any]) -> str | None:
    explicit = _normalized_config_value(
        metadata.get("stock_subclass") or metadata.get("stockSubclass"),
        STOCK_SUBCLASSES,
    )
    if explicit and explicit != "unknown":
        return explicit
    return SECTOR_TO_STOCK_SUBCLASS.get(_clean_value(metadata.get("sector")).lower())


def _pattern_primary(symbol: str) -> str | None:
    if symbol.startswith("^"):
        return "index"
    if symbol.endswith("=F"):
        return "derivative"
    if symbol.endswith("=X"):
        return "forex"
    crypto_match = re.fullmatch(r"([A-Z0-9]{2,10})-(USD|USDT|EUR|GBP)", symbol)
    if crypto_match and crypto_match.group(1) in _CRYPTO_BASES:
        return "crypto"
    if symbol in {"USD", "HKD", "CASH", "MMF"}:
        return "cash"
    return None


def classify_ticker(
    ticker: Any,
    *,
    market_metadata: dict[str, Any] | None = None,
    local_metadata: dict[str, Any] | None = None,
) -> TickerClassification:
    """Resolve one primary class and an optional stock-only subclass."""
    symbol = normalize_ticker_symbol(ticker)
    provider = dict(market_metadata or {})
    local = dict(local_metadata or {})
    override = TICKER_CLASSIFICATION_OVERRIDES.get(symbol, {})

    primary = _provider_primary(provider)
    source = "market_data" if primary else ""

    if not primary:
        primary = _provider_primary(local)
        if primary:
            source = "local_metadata"

    if not primary:
        primary = _local_universe_classes().get(symbol)
        if primary:
            source = "local_metadata"

    if not primary:
        primary = _normalized_config_value(override.get("primary_class"), PRIMARY_TICKER_CLASSES)
        if primary:
            source = "manual_override"

    if not primary:
        primary = _pattern_primary(symbol)
        if primary:
            source = "symbol_pattern"

    if not primary:
        primary = "unknown"
        source = "unknown"

    stock_subclass: str | None = None
    if primary == "stock":
        stock_subclass = _stock_subclass_from_metadata(provider)
        if not stock_subclass:
            stock_subclass = _stock_subclass_from_metadata(local)
        if not stock_subclass:
            stock_subclass = _normalized_config_value(
                override.get("stock_subclass"), STOCK_SUBCLASSES
            )
        stock_subclass = stock_subclass or "unknown"

    return TickerClassification(
        ticker=symbol,
        primary_ticker_class=primary,
        stock_subclass=stock_subclass,
        classification_source=source,
    )


def ticker_classification_dict(
    ticker: Any,
    *,
    market_metadata: dict[str, Any] | None = None,
    local_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return JSON-ready classification fields."""
    return asdict(
        classify_ticker(
            ticker,
            market_metadata=market_metadata,
            local_metadata=local_metadata,
        )
    )
