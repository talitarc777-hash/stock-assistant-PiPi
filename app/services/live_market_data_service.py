"""Near-live market snapshot service.

Note:
- Data is sourced from yfinance and may be delayed.
- This is a near-real-time polling snapshot, not exchange-level streaming.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from threading import Lock
from time import monotonic
from typing import Any

import yfinance as yf

from app.services.market_data import get_price_history
from app.services.hkex_security_metadata import (
    get_hk_security_localized_names,
    get_hk_security_metadata,
)
from app.services.market_config import resolve_security

logger = logging.getLogger(__name__)

_SECTOR_ZH = {
    "Basic Materials": "基礎材料",
    "Communication Services": "通訊服務",
    "Consumer Cyclical": "非必需消費品",
    "Consumer Defensive": "必需消費品",
    "Energy": "能源",
    "Financial Services": "金融服務",
    "Healthcare": "醫療保健",
    "Industrials": "工業",
    "Real Estate": "房地產",
    "Technology": "科技",
    "Utilities": "公用事業",
}

_INDUSTRY_ZH = {
    "Asset Management": "資產管理",
    "Banks - Diversified": "綜合銀行",
    "Consumer Electronics": "消費電子產品",
    "Internet Content & Information": "互聯網內容及資訊服務",
    "Oil & Gas Integrated": "綜合石油及天然氣",
    "Semiconductors": "半導體",
    "Software - Infrastructure": "基礎軟件",
    "Software - Application": "應用軟件",
}

_METADATA_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_METADATA_CACHE_LOCK = Lock()
_METADATA_CACHE_TTL_SECONDS = 24 * 60 * 60


def _get_provider_metadata(provider_symbol: str) -> dict[str, Any]:
    """Return cached Yahoo metadata without downloading price history."""
    now_ts = monotonic()
    with _METADATA_CACHE_LOCK:
        cached_metadata = _METADATA_CACHE.get(provider_symbol)
        info = dict(cached_metadata[1]) if cached_metadata and cached_metadata[0] > now_ts else None
    if info is not None:
        return info
    try:
        info = yf.Ticker(provider_symbol).info or {}
    except Exception as exc:  # pragma: no cover - depends on upstream provider
        logger.info("Security metadata fetch skipped for %s: %s", provider_symbol, exc)
        return {}
    with _METADATA_CACHE_LOCK:
        _METADATA_CACHE[provider_symbol] = (
            monotonic() + _METADATA_CACHE_TTL_SECONDS,
            dict(info),
        )
    return dict(info)


def get_security_name(ticker: str, market: str = "US") -> str | None:
    """Resolve a display name using HKEX first for HK and cached Yahoo for US."""
    identity = resolve_security(ticker, market)
    if identity.market == "HK":
        metadata = get_hk_security_metadata(identity.ticker)
        if metadata is not None and metadata.security_name:
            return str(metadata.security_name)
    info = _get_provider_metadata(identity.provider_symbol)
    name = info.get("longName") or info.get("shortName")
    return str(name) if name else None


def get_security_names(ticker: str, market: str = "US") -> dict[str, str | None]:
    """Return language-specific names without translating US company names."""
    identity = resolve_security(ticker, market)
    english_name = get_security_name(identity.ticker, identity.market)
    chinese_name = None
    chinese_issuer_name = None
    if identity.market == "HK":
        localized = get_hk_security_localized_names(identity.ticker) or {}
        chinese_name = localized.get("security_name_zh")
        chinese_issuer_name = localized.get("issuer_name_zh")
    return {
        "ticker_name_en": english_name,
        "ticker_name_zh": chinese_name or chinese_issuer_name,
        "company_name_zh": chinese_issuer_name,
        "security_name_zh": chinese_name,
    }


def get_security_profile(ticker: str, market: str = "US") -> dict[str, Any]:
    """Resolve a ticker name and descriptive metadata from cached sources."""
    identity = resolve_security(ticker, market)
    hkex_metadata = (
        get_hk_security_metadata(identity.ticker) if identity.market == "HK" else None
    )
    info = _get_provider_metadata(identity.provider_symbol)
    company_name = info.get("longName") or info.get("shortName")
    security_name = hkex_metadata.security_name if hkex_metadata is not None else None
    if not company_name:
        company_name = security_name
    quote_type = info.get("quoteType")
    sector = info.get("sector")
    industry = info.get("industry")
    category = info.get("category")
    fund_family = info.get("fundFamily")
    localized_names = get_security_names(identity.ticker, identity.market)
    return {
        "market": identity.market,
        "ticker": identity.ticker,
        "provider_symbol": identity.provider_symbol,
        "ticker_name": str(company_name or security_name) if (company_name or security_name) else None,
        **localized_names,
        "company_name": str(company_name) if company_name else None,
        "security_name": str(security_name) if security_name else None,
        "security_category": hkex_metadata.category if hkex_metadata is not None else None,
        "security_subcategory": hkex_metadata.subcategory if hkex_metadata is not None else None,
        "ccass_admitted": hkex_metadata.ccass_admitted if hkex_metadata is not None else None,
        "hkex_source_as_of": hkex_metadata.source_as_of if hkex_metadata is not None else None,
        "board_lot": hkex_metadata.board_lot if hkex_metadata is not None else (
            None if identity.market == "HK" else 1
        ),
        "pe_ratio": info.get("trailingPE"),
        "market_cap": info.get("marketCap"),
        "quote_type": str(quote_type) if quote_type else None,
        "sector": str(sector) if sector else None,
        "industry": str(industry) if industry else None,
        "business_summary": str(info.get("longBusinessSummary")) if info.get("longBusinessSummary") else None,
        "business_summary_zh": _build_business_summary_zh(
            company_name=str(company_name) if company_name else None,
            ticker=identity.ticker,
            quote_type=str(quote_type) if quote_type else None,
            sector=str(sector) if sector else None,
            industry=str(industry) if industry else None,
            category=str(category) if category else None,
            fund_family=str(fund_family) if fund_family else None,
        ),
    }


def _build_business_summary_zh(
    *,
    company_name: str | None,
    ticker: str,
    quote_type: str | None,
    sector: str | None,
    industry: str | None,
    category: str | None,
    fund_family: str | None,
) -> str | None:
    """Build a concise Traditional Chinese business description."""
    name = str(company_name or ticker).strip()
    normalized_type = str(quote_type or "").strip().upper()

    if normalized_type == "ETF":
        manager = str(fund_family or "").strip()
        category_text = str(category or "").strip()
        manager_text = f"由 {manager} 管理，" if manager else ""
        focus_text = f"主要投資於「{category_text}」類別的資產" if category_text else "持有一籃子證券"
        return f"{name} 是一隻交易所買賣基金（ETF），{manager_text}{focus_text}。"

    industry_zh = _INDUSTRY_ZH.get(str(industry or "").strip())
    sector_zh = _SECTOR_ZH.get(str(sector or "").strip())
    if industry_zh and sector_zh:
        return f"{name} 主要從事{industry_zh}相關業務，屬於{sector_zh}板塊。"
    if industry_zh:
        return f"{name} 主要從事{industry_zh}相關業務。"
    if sector_zh:
        return f"{name} 主要從事{sector_zh}相關業務。"
    return None


def get_live_market_snapshot(
    ticker: str,
    period: str = "3mo",
    market: str = "US",
) -> dict[str, Any]:
    """Return latest available market snapshot for one ticker."""
    identity = resolve_security(ticker, market)
    symbol = identity.ticker
    provider_symbol = identity.provider_symbol

    history = get_price_history(symbol, period=period, market=identity.market).sort_values("date")
    latest = history.iloc[-1]
    previous = history.iloc[-2] if len(history) >= 2 else latest

    profile = get_security_profile(symbol, identity.market)

    latest_close = float(latest["close"])
    previous_close = float(previous["close"])
    daily_change_pct = ((latest_close / previous_close) - 1) * 100 if previous_close else 0.0

    pe_ratio = profile.get("pe_ratio")
    market_cap = profile.get("market_cap")
    company_name = profile.get("company_name")
    sector = profile.get("sector")
    industry = profile.get("industry")
    business_summary = profile.get("business_summary")
    quote_type = profile.get("quote_type")

    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    logger.info(
        "Live market snapshot market=%s ticker=%s provider_symbol=%s latest_date=%s close=%.2f change_pct=%.3f pe=%s",
        identity.market,
        symbol,
        provider_symbol,
        latest["date"],
        latest_close,
        daily_change_pct,
        pe_ratio,
    )

    return {
        "market": identity.market,
        "ticker": symbol,
        "provider_symbol": provider_symbol,
        "currency": identity.currency,
        "currency_symbol": identity.currency_symbol,
        "ticker_name": profile.get("ticker_name"),
        "ticker_name_en": profile.get("ticker_name_en"),
        "ticker_name_zh": profile.get("ticker_name_zh"),
        "board_lot": profile.get("board_lot"),
        "security_name": profile.get("security_name"),
        "security_name_zh": profile.get("security_name_zh"),
        "security_category": profile.get("security_category"),
        "security_subcategory": profile.get("security_subcategory"),
        "ccass_admitted": profile.get("ccass_admitted"),
        "hkex_source_as_of": profile.get("hkex_source_as_of"),
        "fetched_at_utc": now,
        "price_timestamp": latest["date"].strftime("%Y-%m-%d"),
        "close": latest_close,
        "open": float(latest["open"]),
        "high": float(latest["high"]),
        "low": float(latest["low"]),
        "volume": float(latest["volume"]),
        "daily_change_pct": daily_change_pct,
        "pe_ratio": float(pe_ratio) if pe_ratio is not None else None,
        "market_cap": float(market_cap) if market_cap is not None else None,
        "company_name": str(company_name) if company_name else None,
        "company_name_zh": profile.get("company_name_zh"),
        "quote_type": str(quote_type) if quote_type else None,
        "sector": str(sector) if sector else None,
        "industry": str(industry) if industry else None,
        "business_summary": str(business_summary) if business_summary else None,
        "business_summary_zh": profile.get("business_summary_zh"),
        "data_freshness_note": (
            "Near-live polling snapshot from latest available provider data; "
            "not guaranteed tick-level real-time."
        ),
    }
