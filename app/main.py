"""FastAPI entrypoint for the stock-assistant backend."""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.api.analyze import router as analyze_router
from app.api.backtest import router as backtest_router
from app.api.dashboard import router as dashboard_router
from app.api.discord_link import router as discord_link_router
from app.api.forecast import router as forecast_router
from app.api.market_data import router as market_data_router
from app.api.model_lifecycle import router as model_lifecycle_router
from app.api.model_settings import router as model_settings_router
from app.api.models import router as models_router
from app.api.monthly_contributions import router as monthly_contributions_router
from app.api.news_sentiment import router as news_sentiment_router
from app.api.paper import router as paper_router
from app.api.trader_status import router as trader_status_router
from app.api.user_profile import router as user_profile_router
from app.api.universe import router as universe_router
from app.api.virtual_account import router as virtual_account_router
from app.api.virtual_trader import router as virtual_trader_router
from app.core.settings import get_settings
from app.services.model_lifecycle_scheduler import get_model_lifecycle_scheduler_service
from app.services.model_lifecycle_service import get_model_lifecycle_service
from app.services.trader_scheduler import get_trader_scheduler_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


class HealthResponse(BaseModel):
    """Typed response for the health endpoint."""

    status: str
    app_name: str
    environment: str
    message: str | None = None
    scheduler_started: bool | None = None


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Start/stop background schedulers with app lifecycle."""
    logger = logging.getLogger(__name__)
    trader_scheduler = get_trader_scheduler_service()
    lifecycle_scheduler = get_model_lifecycle_scheduler_service()
    trader_scheduler_started = False
    lifecycle_scheduler_started = False

    try:
        synced = get_model_lifecycle_service().sync_registry_from_saved_artifacts(limit=800)
        logger.info("Model lifecycle startup sync completed discovered=%d", synced)
    except Exception as exc:  # pragma: no cover - defensive startup hardening
        logger.exception("Model lifecycle startup sync failed error=%s", exc)

    try:
        trader_scheduler.start()
        trader_scheduler_started = True
    except Exception as exc:  # pragma: no cover - defensive startup hardening
        logger.exception("Trader scheduler failed to start error=%s", exc)

    try:
        lifecycle_scheduler.start()
        lifecycle_scheduler_started = True
    except Exception as exc:  # pragma: no cover - defensive startup hardening
        logger.exception("Model lifecycle scheduler failed to start error=%s", exc)

    try:
        yield
    finally:
        if lifecycle_scheduler_started:
            try:
                lifecycle_scheduler.stop()
            except Exception as exc:  # pragma: no cover - defensive shutdown hardening
                logger.exception("Model lifecycle scheduler failed to stop error=%s", exc)
        if trader_scheduler_started:
            try:
                trader_scheduler.stop()
            except Exception as exc:  # pragma: no cover - defensive shutdown hardening
                logger.exception("Trader scheduler failed to stop error=%s", exc)


# Create the API app instance.
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Decision-support backend for stock and ETF analysis. "
        "This service gives suggestions, not automated trades."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(market_data_router)
app.include_router(analyze_router)
app.include_router(backtest_router)
app.include_router(dashboard_router)
app.include_router(discord_link_router)
app.include_router(forecast_router)
app.include_router(paper_router)
app.include_router(models_router)
app.include_router(model_settings_router)
app.include_router(model_lifecycle_router)
app.include_router(monthly_contributions_router)
app.include_router(user_profile_router)
app.include_router(universe_router)
app.include_router(virtual_account_router)
app.include_router(virtual_trader_router)
app.include_router(trader_status_router)
app.include_router(news_sentiment_router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health_check() -> HealthResponse:
    """
    Quick endpoint to confirm the API is alive.

    Useful for smoke checks from local scripts, dashboards, or monitors.
    """
    scheduler_health = get_trader_scheduler_service().get_health()
    message = (
        "Server is ready."
        if bool(scheduler_health.get("scheduler_started", False))
        else "Server is starting or temporarily busy. Please retry in a moment."
    )
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        environment=settings.app_env,
        message=message,
        scheduler_started=bool(scheduler_health.get("scheduler_started", False)),
    )


@app.get("/", response_model=HealthResponse, tags=["system"])
def root_health() -> HealthResponse:
    """Return a 200 on root path for platforms that probe `/` by default."""
    return health_check()
