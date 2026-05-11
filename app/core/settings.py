"""Application settings loaded from environment variables."""

from functools import lru_cache

from pydantic import BaseModel
from dotenv import load_dotenv
import os

# Load variables from .env (if present) into process environment.
load_dotenv()


def _parse_csv_env(value: str) -> list[str]:
    """Parse comma/newline separated env values into clean tokens."""
    normalized = value.replace("\n", ",").replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _parse_ticker_csv_env(value: str) -> list[str]:
    """Parse comma/newline separated env values into a clean ticker list."""
    return [item.upper() for item in _parse_csv_env(value)]


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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build settings once and cache for reuse."""
    return Settings(
        app_name=os.getenv("APP_NAME", "Stock Assistant API"),
        app_env=os.getenv("APP_ENV", "development"),
        app_host=os.getenv("APP_HOST", "127.0.0.1"),
        app_port=int(os.getenv("APP_PORT", "8000")),
        profile_db_path=os.getenv("PROFILE_DB_PATH", "data/user_profiles.db"),
        research_data_dir=os.getenv("RESEARCH_DATA_DIR", "data/research"),
        research_models_dir=os.getenv("RESEARCH_MODELS_DIR", "data/models"),
        cors_allow_origins=_parse_csv_env(
            os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
        ),
        cors_allow_origin_regex=os.getenv("CORS_ALLOW_ORIGIN_REGEX") or None,
        default_watchlist=_parse_ticker_csv_env(
            os.getenv("WATCHLIST_TICKERS", "VOO,SPY,QQQ,AAPL,MSFT,NVDA")
        ),
    )
