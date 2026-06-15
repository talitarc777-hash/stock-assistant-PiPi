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
    cors_allow_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    cors_allow_origin_regex: str | None = None
    default_watchlist: list[str] = ["VOO", "SPY", "QQQ", "AAPL", "MSFT", "NVDA"]


_CLOUDFLARE_PAGES_CORS_REGEX = r"^https://([a-z0-9-]+\.)?stock-assistant-pipi\.pages\.dev$"


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
            os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
        ),
        cors_allow_origin_regex=_combine_cors_regex(
            os.getenv("CORS_ALLOW_ORIGIN_REGEX"),
            _CLOUDFLARE_PAGES_CORS_REGEX,
        ),
        default_watchlist=_parse_ticker_csv_env(
            os.getenv("WATCHLIST_TICKERS", "VOO,SPY,QQQ,AAPL,MSFT,NVDA")
        ),
    )
