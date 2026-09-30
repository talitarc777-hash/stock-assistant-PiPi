"""Small independent sampling pass: external evidence is collected even without BUY signals."""
from datetime import UTC, datetime
import logging
import math

from app.core.settings import get_settings
from app.services.context_observation_store import get_context_observation_store
from app.services.external_market_context_service import build_external_market_context
from app.services.market_config import resolve_security
from app.services.universe_service import get_active_universe
from app.services.user_profile_service import get_user_profile_store

logger = logging.getLogger(__name__)


def collect_symbol(ticker, market, now=None, db_path=None):
    from app.services.learning_operations import LearningOperations
    from app.services.external_market_context_service import source_statuses
    from app.services.context_observation_store import FEATURES
    now = now or datetime.now(UTC)
    ops = LearningOperations(db_path)
    if not ops.claim(market, ticker, now):
        return {'attempted': False, 'recorded': False, 'usable': False}
    try:
        context = build_external_market_context(resolve_security(ticker, market).provider_symbol)
        usable = bool(context.get('observation_id')) and any(context.get(f) is not None for f in FEATURES if not f.endswith('_count'))
        ops.finish(market, ticker, usable, source_statuses(context), now)
        return {'attempted': True, 'recorded': bool(context.get('observation_id')), 'usable': usable}
    except Exception:
        ops.finish(market, ticker, False, {}, now)
        logger.warning('Context collection failed market=%s ticker=%s; retry scheduled', market, ticker)
        return {'attempted': True, 'recorded': False, 'usable': False}


def collect_due_context(now: datetime | None = None) -> dict:
    settings = get_settings()
    if not settings.context_archive_enabled or not settings.external_context_enabled:
        return {"enabled": False, "attempted": 0}
    store = get_context_observation_store()
    profiles = get_user_profile_store()
    attempted = recorded = failed = 0
    for market in ("US", "HK"):
        universe = profiles.list_effective_watchlist_tickers(market=market)
        if market == "US":
            universe = list(universe) + list(get_active_universe())
        if getattr(settings, 'prospective_research_enabled', False):
            from app.services.prospective_model_research import PROTOCOL
            universe = list(universe) + PROTOCOL['cohort'][market]
        batch = min(getattr(settings, 'context_collection_batch_max', 8), max(2, math.ceil(len(set(universe)) / 24)))
        for ticker in store.claim_batch(market, universe, now or datetime.now(UTC), limit=batch):
            result = collect_symbol(ticker, market, now, db_path=store.db_path)
            attempted += int(result['attempted'])
            recorded += int(result['recorded'])
            failed += int(result['attempted'] and not result['usable'])
    return {"enabled": True, "attempted": attempted, "recorded": recorded, "failed": failed}
