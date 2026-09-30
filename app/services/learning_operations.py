"""Durable collection admission and sanitized operational diagnostics.

No model selection or evaluation logic belongs here. Leases and backoff survive
API restarts; only allowlisted status codes (never exception text) reach the UI.
"""
from datetime import UTC, datetime, timedelta
import json
import math
from pathlib import Path

from app.core.settings import get_settings
from app.core.sqlite_store import model_store_connection
from app.services.context_observation_store import utc_time


class LearningOperations:
    def __init__(self, path=None):
        self.path = Path(path or get_settings().profile_db_path)
        with model_store_connection(self.path) as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS learning_collection_jobs (
                    market TEXT, ticker TEXT, attempted_at_utc TEXT, next_attempt_utc TEXT,
                    failures INTEGER NOT NULL DEFAULT 0, status TEXT, source_status_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(market,ticker));
                CREATE TABLE IF NOT EXISTS learning_provider_budget (
                    provider TEXT, day TEXT, used INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(provider,day));
                CREATE TABLE IF NOT EXISTS learning_heartbeat (
                    name TEXT PRIMARY KEY, at_utc TEXT, status TEXT);
            ''')

    def heartbeat(self, name, status, now=None):
        with model_store_connection(self.path) as conn:
            conn.execute('INSERT OR REPLACE INTO learning_heartbeat VALUES (?,?,?)',
                         (name, utc_time(now or datetime.now(UTC)).isoformat(), status))

    def claim(self, market, ticker, now):
        with model_store_connection(self.path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM learning_collection_jobs WHERE market=? AND ticker=?', (market, ticker)).fetchone()
            if row and row['next_attempt_utc'] > now.isoformat():
                return False
            conn.execute('''INSERT INTO learning_collection_jobs(market,ticker,attempted_at_utc,next_attempt_utc,status)
                VALUES (?,?,?,?,'collecting') ON CONFLICT(market,ticker) DO UPDATE SET
                attempted_at_utc=excluded.attempted_at_utc,next_attempt_utc=excluded.next_attempt_utc,status=excluded.status''',
                (market, ticker, now.isoformat(), (now + timedelta(minutes=15)).isoformat()))
        return True

    def finish(self, market, ticker, usable, statuses, now):
        from app.services.external_market_context_service import SOURCE_FIELDS
        allowed = {'usable', 'empty', 'unavailable', 'disabled', 'missing_credentials',
                   'unsupported_market', 'unverified_entity', 'rate_limited', 'budget_exhausted'}
        safe = {k: v for k, v in statuses.items() if k in SOURCE_FIELDS and v in allowed}
        with model_store_connection(self.path) as conn:
            row = conn.execute('SELECT failures FROM learning_collection_jobs WHERE market=? AND ticker=?', (market, ticker)).fetchone()
            failures = 0 if usable else int(row['failures'] if row else 0) + 1
            delay = 60 if usable else min(360, 15 * 2 ** min(5, failures - 1))
            conn.execute('UPDATE learning_collection_jobs SET status=?,failures=?,next_attempt_utc=?,source_status_json=? WHERE market=? AND ticker=?',
                ('usable' if usable else 'no_usable_context', failures, (now + timedelta(minutes=delay)).isoformat(), json.dumps(safe), market, ticker))

    def spend_budget(self, provider, limit, now=None):
        day = utc_time(now or datetime.now(UTC)).date().isoformat()
        with model_store_connection(self.path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT OR IGNORE INTO learning_provider_budget VALUES (?,?,0)', (provider, day))
            changed = conn.execute('UPDATE learning_provider_budget SET used=used+1 WHERE provider=? AND day=? AND used<?',
                                   (provider, day, limit)).rowcount
        return bool(changed)


def collection_universe(market):
    from app.services.user_profile_service import get_user_profile_store
    from app.services.universe_service import get_active_universe
    from app.services.market_config import resolve_security
    from app.services.prospective_model_research import PROTOCOL
    tickers = list(get_user_profile_store().list_effective_watchlist_tickers(market=market))
    if market == 'US':
        tickers += list(get_active_universe())
    if get_settings().prospective_research_enabled:
        tickers += PROTOCOL['cohort'][market]
    return sorted({resolve_security(t, market).ticker for t in tickers})


def learning_health():
    from app.services.context_observation_store import get_context_observation_store
    from app.services.prospective_model_research import ProspectiveModelResearch
    from app.services.model_lifecycle_scheduler import get_model_lifecycle_scheduler_service
    from app.services.external_market_context_service import source_configuration
    now = datetime.now(UTC)
    settings = get_settings()
    ops = LearningOperations()
    with model_store_connection(ops.path) as conn:
        jobs = [dict(r) for r in conn.execute('SELECT * FROM learning_collection_jobs ORDER BY market,ticker')]
        heartbeat = conn.execute("SELECT * FROM learning_heartbeat WHERE name='research'").fetchone()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        admission = [dict(r) for r in conn.execute('SELECT last_reason,COUNT(*) AS count FROM model_training_evidence GROUP BY last_reason')] if 'model_training_evidence' in tables else []
    for job in jobs:
        job['sources'] = json.loads(job.pop('source_status_json'))
    scheduler = get_model_lifecycle_scheduler_service().get_health()
    research = ProspectiveModelResearch().status()
    coverage = get_context_observation_store().status()
    blockers = []
    for flag in ('external_context_enabled', 'context_archive_enabled', 'model_feedback_enabled', 'prospective_research_enabled'):
        if not getattr(settings, flag):
            blockers.append(flag + '_disabled')
    if settings.model_feedback_horizon_days != 5:
        blockers.append('five_session_feedback_required')
    if not scheduler.get('scheduler_started'):
        blockers.append('lifecycle_scheduler_not_started')
    if research['protocol_matches'] is False:
        blockers.append('protocol_changed_requires_review')
    if not heartbeat or now - utc_time(heartbeat['at_utc']) > timedelta(hours=2):
        blockers.append('research_heartbeat_missing_or_stale')
    if heartbeat and heartbeat['status'] != 'completed':
        blockers.append('research_cycle_' + heartbeat['status'])
    if not any(job['status'] == 'usable' and utc_time(job['attempted_at_utc']) > now - timedelta(hours=24) for job in jobs):
        blockers.append('no_recent_usable_context')
    plans = {}
    for market in ('US', 'HK'):
        size = len(collection_universe(market))
        batch = min(settings.context_collection_batch_max, max(2, math.ceil(size / 24)))
        plans[market] = {'universe_tickers': size, 'hourly_batch': batch,
                         'estimated_rotation_hours': math.ceil(size / batch) if size else 0,
                         'cohort_daily_target': 4, 'note': 'capacity estimate; retries/provider gaps can delay coverage'}
        if size and not any(j['market'] == market and j['status'] == 'usable' and utc_time(j['attempted_at_utc']) > now - timedelta(hours=24) for j in jobs):
            blockers.append(market + '_no_recent_usable_context')
        latest = research['readiness'][market]['snapshot_counts']['latest']
        if latest and now - utc_time(latest) > timedelta(days=7):
            blockers.append(market + '_research_price_snapshots_stale')
    return {'status': 'attention_needed' if blockers else 'collecting_or_evaluating',
            'blockers': blockers, 'checked_at_utc': now.isoformat(), 'heartbeat': dict(heartbeat) if heartbeat else None,
            'sources': source_configuration(), 'plans': plans, 'collection': jobs,
            'coverage': coverage, 'research': research, 'training_admission': admission,
            'context_auto_promotion': False}
