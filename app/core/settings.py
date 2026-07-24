"""Application settings loaded from environment variables."""

from functools import lru_cache
import logging
from pathlib import Path
import sqlite3

from pydantic import BaseModel
from dotenv import load_dotenv
import os

# Load variables from .env (if present) into process environment.
load_dotenv()

logger = logging.getLogger(__name__)


def _parse_csv_env(value: str) -> list[str]:
    """Parse comma/newline separated env values into clean tokens."""
    normalized = value.replace("\n", ",").replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _parse_ticker_csv_env(value: str) -> list[str]:
    """Parse comma/newline separated env values into a clean ticker list."""
    return [item.upper() for item in _parse_csv_env(value)]


def _parse_bool_env(value: str | None, default: bool = False) -> bool:
    """Parse common truthy/falsy environment values."""
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _combine_cors_regex(*patterns: str | None) -> str | None:
    """Combine optional CORS regex patterns without losing deployment defaults."""
    clean_patterns = [pattern.strip() for pattern in patterns if pattern and pattern.strip()]
    if not clean_patterns:
        return None
    if len(clean_patterns) == 1:
        return clean_patterns[0]
    return "|".join(f"(?:{pattern})" for pattern in clean_patterns)


def _backup_sqlite_database(source_path: Path, target_path: Path) -> None:
    """Create a consistent SQLite copy, including data visible through WAL."""
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _resolve_profile_db_path(app_env: str, configured_path: str) -> str:
    """Keep production account data outside the replaceable application checkout."""
    configured = Path(configured_path).expanduser()
    if app_env.lower() != "production" or configured.is_absolute():
        return str(configured)

    persistent_root = Path(
        os.getenv(
            "PERSISTENT_DATA_DIR",
            str(Path.home() / ".local" / "share" / "stock-assistant"),
        )
    ).expanduser()
    target = persistent_root / configured.name
    legacy_source = Path.cwd() / configured

    if not target.exists() and legacy_source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        _backup_sqlite_database(legacy_source, target)
        logger.info(
            "Migrated profile database from replaceable path %s to persistent path %s",
            legacy_source,
            target,
        )

    return str(target)


class Settings(BaseModel):
    """Typed runtime settings for the API."""

    app_name: str = "Stock Assistant API"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    profile_db_path: str = "data/user_profiles.db"
    research_data_dir: str = "data/research"
    research_models_dir: str = "data/models"
    cors_allow_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://cowbox.dpdns.org",
        "https://tail8919df.ts.net",
    ]
    cors_allow_origin_regex: str | None = None
    default_watchlist: list[str] = ["VOO", "SPY", "QQQ", "AAPL", "MSFT", "NVDA"]
    external_context_enabled: bool = True
    external_context_timeout_seconds: float = 2.5
    alpha_vantage_api_key: str | None = None
    reddit_context_enabled: bool = True
    reddit_context_subreddits: list[str] = ["stocks", "investing"]
    reddit_context_limit: int = 8
    sec_context_enabled: bool = True
    sec_user_agent: str = "StockAssistantPiPi/1.0 contact@example.com"
    discord_webhook_url: str | None = None
    real_market_discord_alert_enabled: bool = True
    real_market_alert_window_minutes: int = 15
    real_market_large_value_threshold: float = 10_000_000.0
    real_market_volume_spike_multiplier: float = 3.0
    real_market_price_move_threshold_pct: float = 1.5
    real_market_sudden_move_threshold_pct: float = 10.0
    real_market_min_window_volume: float = 100_000.0
    real_market_alert_ticker_limit: int = 40
    model_feedback_enabled: bool = True
    model_feedback_horizon_days: int = 5
    model_feedback_min_samples: int = 8
    model_feedback_promotion_weight: float = 0.35
    context_feedback_max_adjustment: float = 8.0
    hkd_per_usd_rate: float = 7.8


_CLOUDFLARE_PAGES_PREVIEW_CORS_REGEX = r"^https://([a-z0-9-]+\.)?stock-assistant-pipi\.pages\.dev$"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build settings once and cache for reuse."""
    app_env = os.getenv("APP_ENV", "development")
    profile_db_path = _resolve_profile_db_path(
        app_env=app_env,
        configured_path=os.getenv("PROFILE_DB_PATH", "data/user_profiles.db"),
    )
    return Settings(
        app_name=os.getenv("APP_NAME", "Stock Assistant API"),
        app_env=app_env,
        app_host=os.getenv("APP_HOST", "127.0.0.1"),
        app_port=int(os.getenv("APP_PORT", "8000")),
        profile_db_path=profile_db_path,
        research_data_dir=os.getenv("RESEARCH_DATA_DIR", "data/research"),
        research_models_dir=os.getenv("RESEARCH_MODELS_DIR", "data/models"),
        cors_allow_origins=_parse_csv_env(
            os.getenv(
                "CORS_ALLOW_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173,"
                "https://cowbox.dpdns.org,https://tail8919df.ts.net",
            )
        ),
        cors_allow_origin_regex=_combine_cors_regex(
            os.getenv("CORS_ALLOW_ORIGIN_REGEX"),
            _CLOUDFLARE_PAGES_PREVIEW_CORS_REGEX,
        ),
        default_watchlist=_parse_ticker_csv_env(
            os.getenv("WATCHLIST_TICKERS", "VOO,SPY,QQQ,AAPL,MSFT,NVDA")
        ),
        external_context_enabled=_parse_bool_env(os.getenv("EXTERNAL_CONTEXT_ENABLED"), True),
        external_context_timeout_seconds=float(os.getenv("EXTERNAL_CONTEXT_TIMEOUT_SECONDS", "2.5")),
        alpha_vantage_api_key=(os.getenv("ALPHA_VANTAGE_API_KEY") or "").strip() or None,
        reddit_context_enabled=_parse_bool_env(os.getenv("REDDIT_CONTEXT_ENABLED"), True),
        reddit_context_subreddits=_parse_csv_env(os.getenv("REDDIT_CONTEXT_SUBREDDITS", "stocks,investing")),
        reddit_context_limit=max(1, int(os.getenv("REDDIT_CONTEXT_LIMIT", "8"))),
        sec_context_enabled=_parse_bool_env(os.getenv("SEC_CONTEXT_ENABLED"), True),
        sec_user_agent=os.getenv(
            "SEC_USER_AGENT",
            "StockAssistantPiPi/1.0 contact@example.com",
        ),
        discord_webhook_url=(os.getenv("DISCORD_WEBHOOK_URL") or "").strip() or None,
        real_market_discord_alert_enabled=_parse_bool_env(
            os.getenv("REAL_MARKET_DISCORD_ALERT_ENABLED"),
            True,
        ),
        real_market_alert_window_minutes=max(
            5,
            int(os.getenv("REAL_MARKET_ALERT_WINDOW_MINUTES", "15")),
        ),
        real_market_large_value_threshold=max(
            0.0,
            float(os.getenv("REAL_MARKET_LARGE_VALUE_THRESHOLD", "10000000")),
        ),
        real_market_volume_spike_multiplier=max(
            1.0,
            float(os.getenv("REAL_MARKET_VOLUME_SPIKE_MULTIPLIER", "3")),
        ),
        real_market_price_move_threshold_pct=max(
            0.0,
            float(os.getenv("REAL_MARKET_PRICE_MOVE_THRESHOLD_PCT", "1.5")),
        ),
        real_market_sudden_move_threshold_pct=max(
            0.1,
            float(os.getenv("REAL_MARKET_SUDDEN_MOVE_THRESHOLD_PCT", "10.0")),
        ),
        real_market_min_window_volume=max(
            0.0,
            float(os.getenv("REAL_MARKET_MIN_WINDOW_VOLUME", "100000")),
        ),
        real_market_alert_ticker_limit=max(
            1,
            int(os.getenv("REAL_MARKET_ALERT_TICKER_LIMIT", "40")),
        ),
        model_feedback_enabled=_parse_bool_env(os.getenv("MODEL_FEEDBACK_ENABLED"), True),
        model_feedback_horizon_days=max(
            1,
            int(os.getenv("MODEL_FEEDBACK_HORIZON_DAYS", "5")),
        ),
        model_feedback_min_samples=max(
            3,
            int(os.getenv("MODEL_FEEDBACK_MIN_SAMPLES", "8")),
        ),
        model_feedback_promotion_weight=max(
            0.0,
            min(0.5, float(os.getenv("MODEL_FEEDBACK_PROMOTION_WEIGHT", "0.35"))),
        ),
        context_feedback_max_adjustment=max(
            0.0,
            min(15.0, float(os.getenv("CONTEXT_FEEDBACK_MAX_ADJUSTMENT", "8"))),
        ),
        hkd_per_usd_rate=max(
            0.0001,
            float(os.getenv("HKD_PER_USD_RATE", "7.8")),
        ),
    )
