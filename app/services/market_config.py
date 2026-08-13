"""Market identity, ticker normalization, and exchange-specific trading rules.

The application stores a security as ``market + ticker``. Provider symbols are
derived at the market-data boundary and are never used as the database identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re


SUPPORTED_MARKETS = ("US", "HK")


class MarketValidationError(ValueError):
    """Raised when a market or ticker cannot be normalized safely."""


@dataclass(frozen=True)
class MarketConfig:
    market: str
    currency: str
    currency_symbol: str
    timezone: str
    default_benchmark: str
    default_tickers: tuple[str, ...]


@dataclass(frozen=True)
class SecurityIdentity:
    market: str
    ticker: str
    provider_symbol: str
    currency: str
    currency_symbol: str


MARKET_CONFIGS: dict[str, MarketConfig] = {
    "US": MarketConfig(
        market="US",
        currency="USD",
        currency_symbol="$",
        timezone="America/New_York",
        default_benchmark="VOO",
        default_tickers=(),
    ),
    "HK": MarketConfig(
        market="HK",
        currency="HKD",
        currency_symbol="HK$",
        timezone="Asia/Hong_Kong",
        default_benchmark="2800",
        default_tickers=("0005", "0700", "1810", "3690", "9988"),
    ),
}

_US_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-^=]{0,14}$")
_HK_TICKER_PATTERN = re.compile(r"^(?P<code>\d{1,4})(?:\.HK)?$", re.IGNORECASE)


def normalize_market(market: str | None = None) -> str:
    value = str(market or "US").strip().upper()
    if value not in MARKET_CONFIGS:
        raise MarketValidationError("market must be US or HK.")
    return value


def normalize_hk_ticker(ticker: str) -> str:
    """Return the four-digit database identity for an HK security."""
    value = str(ticker or "").strip()
    match = _HK_TICKER_PATTERN.fullmatch(value)
    if match is None:
        raise MarketValidationError(
            "HK ticker must contain 1 to 4 digits, optionally followed by .HK."
        )
    code = int(match.group("code"))
    if code <= 0 or code > 9999:
        raise MarketValidationError("HK ticker must be between 0001 and 9999.")
    return f"{code:04d}"


def resolve_security(ticker: str, market: str | None = None) -> SecurityIdentity:
    clean_market = normalize_market(market)
    raw = str(ticker or "").strip().upper()
    if clean_market == "HK":
        clean_ticker = normalize_hk_ticker(raw)
        provider_symbol = f"{clean_ticker}.HK"
    else:
        if not raw or _US_TICKER_PATTERN.fullmatch(raw) is None:
            raise MarketValidationError("Invalid US ticker format.")
        clean_ticker = raw
        provider_symbol = raw
    config = MARKET_CONFIGS[clean_market]
    return SecurityIdentity(
        market=clean_market,
        ticker=clean_ticker,
        provider_symbol=provider_symbol,
        currency=config.currency,
        currency_symbol=config.currency_symbol,
    )


def resolve_model_identity(ticker: str, market: str | None = None) -> SecurityIdentity:
    """Resolve a security model or the market-wide ``GLOBAL`` model identity.

    ``GLOBAL`` is not a tradable symbol.  It is allowed only at model-storage
    and registry boundaries, where it represents a pooled, scale-independent
    model trained across multiple securities in one market.
    """
    clean_market = normalize_market(market)
    if str(ticker or "").strip().upper() == "GLOBAL":
        config = MARKET_CONFIGS[clean_market]
        return SecurityIdentity(
            market=clean_market,
            ticker="GLOBAL",
            provider_symbol="GLOBAL",
            currency=config.currency,
            currency_symbol=config.currency_symbol,
        )
    return resolve_security(ticker, clean_market)


def model_security_root(base_dir, market: str, ticker: str):
    """Return collision-safe storage root while retaining legacy US paths."""
    identity = resolve_model_identity(ticker, market)
    if identity.market == "US":
        return base_dir / identity.ticker
    return base_dir / identity.market / identity.ticker


HK_PHASE_2_EFFECTIVE_DATE = date(2026, 8, 3)


def hk_minimum_spread(price: float | Decimal, *, on_date: date | None = None) -> Decimal:
    """Return the HKEX equity minimum spread effective from 3 August 2026."""
    try:
        value = Decimal(str(price))
    except (InvalidOperation, ValueError) as exc:
        raise MarketValidationError("price must be numeric.") from exc
    if value < Decimal("0.01") or value > Decimal("9995"):
        raise MarketValidationError("HK equity price must be between HK$0.01 and HK$9,995.")
    effective_date = on_date or date.today()
    if effective_date < HK_PHASE_2_EFFECTIVE_DATE:
        raise MarketValidationError("Only the current HKEX Phase 2 spread table is supported.")
    bands = (
        (Decimal("0.25"), Decimal("0.001")),
        (Decimal("10"), Decimal("0.005")),
        (Decimal("20"), Decimal("0.01")),
        (Decimal("50"), Decimal("0.02")),
        (Decimal("100"), Decimal("0.05")),
        (Decimal("200"), Decimal("0.1")),
        (Decimal("500"), Decimal("0.2")),
        (Decimal("1000"), Decimal("0.5")),
        (Decimal("2000"), Decimal("1")),
        (Decimal("5000"), Decimal("2")),
        (Decimal("9995"), Decimal("5")),
    )
    for upper, spread in bands:
        if value < upper or upper == Decimal("9995") and value <= upper:
            return spread
    raise MarketValidationError("No HKEX spread is available for this price.")


def is_valid_hk_price(price: float | Decimal, *, on_date: date | None = None) -> bool:
    value = Decimal(str(price))
    spread = hk_minimum_spread(value, on_date=on_date)
    return value % spread == 0
