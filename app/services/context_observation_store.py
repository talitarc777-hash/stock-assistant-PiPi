"""Prospective external features: observed availability, never backfilled prices.

Only derived numeric features and source names are retained. Article bodies,
provider exception messages and credentials are deliberately outside this schema.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

from app.core.settings import get_settings
from app.core.sqlite_store import model_store_connection
from app.services.market_config import MARKET_CONFIGS, resolve_security
from zoneinfo import ZoneInfo

FEATURES = (
    "news_sentiment_score", "news_article_count", "social_sentiment_score",
    "social_mention_count", "social_engagement_score", "analyst_revision_score",
    "analyst_event_count", "analyst_consensus_score", "official_regulatory_risk_score",
    "official_event_count", "earnings_call_tone_score", "alpha_news_sentiment_score",
)
SCHEMA_VERSION = 1
COUNT_SOURCES = {
    "news_article_count": "yfinance_news_lexicon_v1",
    "social_mention_count": "reddit_social_search",
    "analyst_event_count": "yfinance_analyst_consensus",
    "official_event_count": "sec_edgar_filings",
}


def utc_time(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        raise ValueError("Availability timestamps require an explicit timezone")
    return parsed.astimezone(UTC)


class ContextObservationStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS external_context_observations (
                    id INTEGER PRIMARY KEY, market TEXT NOT NULL, ticker TEXT NOT NULL,
                    source_observed_at_utc TEXT NOT NULL, available_at_utc TEXT NOT NULL,
                    expires_at_utc TEXT NOT NULL, schema_version INTEGER NOT NULL,
                    features_json TEXT NOT NULL, sources_json TEXT NOT NULL,
                    missing_sources_json TEXT NOT NULL, content_hash TEXT NOT NULL,
                    UNIQUE(market,ticker,source_observed_at_utc,content_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_context_asof ON external_context_observations
                    (market,ticker,available_at_utc);
                CREATE TABLE IF NOT EXISTS context_collection_cursor (
                    market TEXT PRIMARY KEY, cursor INTEGER NOT NULL DEFAULT 0,
                    next_run_at_utc TEXT NOT NULL
                );
            """)

    def connect(self):
        return model_store_connection(self.db_path)

    def record(self, ticker: str, market: str, context: dict, *, now: datetime | None = None) -> int:
        identity = resolve_security(ticker, market)
        available = utc_time(now or datetime.now(UTC))
        observed = utc_time(context.get("fetched_at_utc") or available)
        if observed > available:
            raise ValueError("Source observation cannot be in the future")
        features = {}
        for key in FEATURES:
            value = context.get(key)
            # Live payloads initialize counts to zero even if a feed fails.
            # Unavailable data must not become a zero-attention observation.
            if key in COUNT_SOURCES and COUNT_SOURCES[key] not in (context.get("sources_available") or []):
                value = None
            try:
                value = float(value) if value is not None else None
                features[key] = value if value is not None and math.isfinite(value) else None
            except (TypeError, ValueError):
                features[key] = None
        sources = sorted(set(context.get("sources_available") or []))
        missing = sorted(set(context.get("missing_sources") or []))
        encoded = json.dumps(features, sort_keys=True, allow_nan=False)
        fingerprint = hashlib.sha256(json.dumps([features, sources, missing], sort_keys=True).encode()).hexdigest()
        with self.connect() as conn:
            conn.execute("""INSERT OR IGNORE INTO external_context_observations
                (market,ticker,source_observed_at_utc,available_at_utc,expires_at_utc,
                 schema_version,features_json,sources_json,missing_sources_json,content_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (
                identity.market, identity.ticker, observed.isoformat(), available.isoformat(),
                (observed + timedelta(hours=24)).isoformat(), SCHEMA_VERSION, encoded,
                json.dumps(sources), json.dumps(missing), fingerprint,
            ))
            row = conn.execute("""SELECT id FROM external_context_observations
                WHERE market=? AND ticker=? AND source_observed_at_utc=? AND content_hash=?""",
                (identity.market, identity.ticker, observed.isoformat(), fingerprint)).fetchone()
        return int(row["id"])

    def as_of(self, ticker: str, market: str, at: str | datetime) -> dict | None:
        identity = resolve_security(ticker, market)
        cutoff = utc_time(at).isoformat()
        with self.connect() as conn:
            row = conn.execute("""SELECT * FROM external_context_observations
                WHERE market=? AND ticker=? AND available_at_utc<=? AND expires_at_utc>?
                  AND schema_version=? ORDER BY available_at_utc DESC,id DESC LIMIT 1""",
                (identity.market, identity.ticker, cutoff, cutoff, SCHEMA_VERSION)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["features"] = json.loads(result.pop("features_json"))
        result["sources"] = json.loads(result.pop("sources_json"))
        result["missing_sources"] = json.loads(result.pop("missing_sources_json"))
        return result

    def claim_batch(self, market: str, universe: list[str], now: datetime, limit: int = 2) -> list[str]:
        """One bounded hourly batch per market, including failures; cursor survives restarts."""
        symbols = sorted({resolve_security(t, market).ticker for t in universe})
        if not symbols:
            return []
        current = utc_time(now)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM context_collection_cursor WHERE market=?", (market,)).fetchone()
            if row and utc_time(row["next_run_at_utc"]) > current:
                return []
            cursor = int(row["cursor"]) if row else 0
            count = min(max(1, limit), len(symbols))
            selected = [symbols[(cursor + i) % len(symbols)] for i in range(count)]
            conn.execute("""INSERT INTO context_collection_cursor VALUES (?,?,?)
                ON CONFLICT(market) DO UPDATE SET cursor=excluded.cursor,next_run_at_utc=excluded.next_run_at_utc""",
                (market, (cursor + count) % len(symbols), (current + timedelta(hours=1)).isoformat()))
        return selected

    def status(self) -> dict:
        with self.connect() as conn:
            rows = conn.execute("""SELECT market,COUNT(*) AS observations,COUNT(DISTINCT ticker) AS tickers,
                MIN(available_at_utc) AS first_available_at_utc,MAX(available_at_utc) AS latest_available_at_utc
                FROM external_context_observations GROUP BY market""").fetchall()
            coverage = conn.execute("SELECT market," + ','.join(
                f"SUM(CASE WHEN json_extract(features_json,'$.{field}') IS NOT NULL THEN 1 ELSE 0 END) AS {field}"
                for field in FEATURES
            ) + " FROM external_context_observations GROUP BY market").fetchall()
            observations = [dict(r) for r in conn.execute('SELECT * FROM external_context_observations ORDER BY id DESC LIMIT 10000')]
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            trained = bool(conn.execute("SELECT 1 FROM research_model_rounds WHERE versions_json!='{}' LIMIT 1").fetchone()) if 'research_model_rounds' in tables else False
        return {"schema_version": SCHEMA_VERSION, "historical_backfill": False,
                "used_for_model_training": trained, "markets": [dict(r) for r in rows],
                "used_for_experimental_training": trained,
                "production_feature_training_enabled": False,
                "feature_observation_counts": [dict(r) for r in coverage],
                "quality_window": "latest_10000_snapshots; markets and feature_observation_counts are lifetime totals",
                "quality": summarize_context_coverage(observations)}


def summarize_context_coverage(rows, now=None):
    """Snapshot availability is measurable; vendor uptime/content truth is not."""
    now = utc_time(now or datetime.now(UTC))
    result = {}
    for market in ('US', 'HK'):
        sample = [r for r in rows if r['market'] == market and utc_time(r['available_at_utc']) <= now]
        tickers = {}
        sources = {}
        feature_counts = {key: 0 for key in FEATURES}
        dates = set()
        for row in sample:
            available = utc_time(row['available_at_utc'])
            date = available.astimezone(ZoneInfo(MARKET_CONFIGS[market].timezone)).date().isoformat()
            dates.add(date)
            item = tickers.setdefault(row['ticker'], {'observations': 0, 'dates': set(), 'latest': available})
            item['observations'] += 1
            item['dates'].add(date)
            item['latest'] = max(item['latest'], available)
            features = json.loads(row['features_json'])
            for key in feature_counts:
                if features.get(key) is not None:
                    feature_counts[key] += 1
            available_sources = set(json.loads(row['sources_json']))
            missing_sources = set(json.loads(row['missing_sources_json']))
            for source in available_sources | missing_sources:
                counts = sources.setdefault(source, {'available_snapshots': 0, 'reported_missing_snapshots': 0})
                counts['available_snapshots'] += int(source in available_sources)
                counts['reported_missing_snapshots'] += int(source in missing_sources)
        result[market] = {'observations': len(sample), 'distinct_observation_dates': len(dates),
            'first_date': min(dates) if dates else None, 'last_date': max(dates) if dates else None,
            'fresh_snapshots': sum(utc_time(r['expires_at_utc']) > now for r in sample),
            'feature_missing_fraction': {k: 1 - v / len(sample) if sample else None for k, v in feature_counts.items()},
            'source_availability': sources,
            'tickers': {t: {'observations': v['observations'], 'distinct_dates': len(v['dates']),
                           'latest_age_hours': (now - v['latest']).total_seconds()/3600} for t, v in tickers.items()},
            'reliability_note': 'snapshot availability, not verified provider uptime or sentiment accuracy'}
    return result


@lru_cache(maxsize=4)
def _store(path: str) -> ContextObservationStore:
    return ContextObservationStore(path)


def get_context_observation_store() -> ContextObservationStore:
    return _store(str(get_settings().profile_db_path))
