"""Fixed, non-trading price/context experiment using the application's model services.

Only orchestration and frozen input snapshots live here. Features, fitting,
validation, immutable model identity and outcome settlement retain their existing
canonical owners. Research evidence is isolated from production calibration.
"""
from datetime import UTC, datetime, timedelta
import hashlib
import inspect
from io import StringIO
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.core.settings import get_settings
from app.core.sqlite_store import model_store_connection
from app.services.context_observation_store import ContextObservationStore, utc_time
from app.services.market_config import MARKET_CONFIGS
from app.services.model_feedback_service import ModelFeedbackService
from app.services.model_training import (
    FEATURE_EXCLUDE_COLUMNS, prepare_stationary_feature_dataset, train_baseline_model,
)
from app.services.model_lifecycle_service import ModelLifecycleService
from app.services.model_results import load_trained_model_bundle
from app.services.research_pipeline import build_feature_dataset
from app.services.training_evidence import frame_fingerprint


PROTOCOL = {
    'id': 'pit-context-ridge-v1', 'version': 1,
    'cohort': {'US': ['AAPL', 'MSFT', 'JPM', 'XOM'], 'HK': ['0005', '0700', '1810', '9988']},
    'benchmark': {'US': 'VOO', 'HK': '2800'},
    'target': 'target_5d_return', 'horizon_sessions': 5,
    'target_definition': 'raw_close_return_pct_from_observed_close_after_5_later_market_rows',
    'model': 'ridge_regression', 'ridge_alpha': 10.0,
    'arms': ['price_only', 'price_context'],
    'external_candidates': ['news_sentiment_score', 'analyst_consensus_score', 'social_mention_count'],
    'minimum_training_dates': 120, 'minimum_dates_per_ticker': 90,
    'minimum_feature_coverage': 0.8, 'minimum_ticker_feature_coverage': 0.5,
    'maximum_external_features': 2, 'locked_test_dates': 120,
    'minimum_forward_dates_per_ticker': 90,
    'round_trip_cost_pct': {'US': 0.25, 'HK': 0.75},
    'buy_threshold_pct': 1.0,
    'baselines': ['cash', 'always_long', 'benchmark', 'momentum_20d'],
    'metrics': ['direction_accuracy', 'balanced_accuracy', 'mae_pct', 'brier_score',
                'net_return_per_opportunity_pct', 'nonoverlapping_path_returns'],
    'retraining': 'only_after_entire_120_date_forward_round_settles; rolling_last_120_matured_dates',
    'production_enabled': False,
}


def protocol_hash():
    return hashlib.sha256(json.dumps(effective_protocol(), sort_keys=True).encode()).hexdigest()


def effective_protocol():
    # A deployment changing feature/training semantics must not silently alter
    # an in-flight experiment, even if column names and hyperparameters match.
    directory = Path(__file__).parent
    code = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ('model_training.py', 'research_pipeline.py',
                         'market_regime.py', 'indicators.py', 'market_data.py', 'market_config.py',
                         'external_market_context_service.py',
                         'news_sentiment.py', 'model_feedback_service.py', 'model_results.py',
                         'paired_model_evidence.py')}
    # UI/status/retry changes must not restart a months-long experiment. Freeze
    # the actual snapshot, label and comparison methods, not this whole module.
    for function in _CONTRACT_FUNCTIONS:
        code[function.__qualname__] = hashlib.sha256(inspect.getsource(function).encode()).hexdigest()
    import sklearn
    return {**PROTOCOL, 'implementation_hashes': code,
            'library_versions': {'sklearn': sklearn.__version__, 'pandas': pd.__version__, 'numpy': np.__version__}}


def asof_external_features(store, ticker, market, at):
    """Single as-of contract shared by research training snapshots and inference."""
    snapshot = store.as_of(ticker, market, at)
    features = (snapshot or {}).get('features') or {}
    return {f'context_{name}': features.get(name) for name in PROTOCOL['external_candidates']}, (snapshot or {}).get('id')


class ProspectiveModelResearch:
    def __init__(self, db_path=None):
        self.path = Path(db_path or get_settings().profile_db_path)
        self.context = ContextObservationStore(str(self.path))
        self.feedback = ModelFeedbackService(str(self.path))
        with model_store_connection(self.path) as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS research_protocols (
                    protocol_id TEXT PRIMARY KEY, protocol_hash TEXT NOT NULL,
                    protocol_json TEXT NOT NULL, started_at_utc TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_feature_snapshots (
                    protocol_id TEXT, market TEXT, ticker TEXT, decision_date TEXT,
                    available_at_utc TEXT NOT NULL, price REAL NOT NULL,
                    technical_json TEXT NOT NULL, context_json TEXT NOT NULL, context_observation_id INTEGER,
                    PRIMARY KEY(protocol_id,market,ticker,decision_date));
                CREATE TABLE IF NOT EXISTS research_model_rounds (
                    protocol_id TEXT, market TEXT, round_number INTEGER, status TEXT NOT NULL,
                    started_at_utc TEXT NOT NULL, attempt_at_utc TEXT NOT NULL,
                    dataset_hash TEXT NOT NULL, data_json TEXT NOT NULL, external_features_json TEXT NOT NULL,
                    versions_json TEXT NOT NULL DEFAULT '{}', result_json TEXT,
                    PRIMARY KEY(protocol_id,market,round_number));
                CREATE TABLE IF NOT EXISTS research_capture_attempts (
                    protocol_id TEXT,market TEXT,ticker TEXT,day TEXT,next_attempt_utc TEXT,
                    PRIMARY KEY(protocol_id,market,ticker,day));
                CREATE TABLE IF NOT EXISTS research_monitoring_checkpoints (
                    protocol_id TEXT,market TEXT,round_number INTEGER,dates INTEGER,result_json TEXT,
                    PRIMARY KEY(protocol_id,market,round_number,dates));
            ''')
            current_hash = protocol_hash()
            prior = conn.execute('SELECT * FROM research_protocols WHERE protocol_id=?', (PROTOCOL['id'],)).fetchone()
            self.protocol_id = (PROTOCOL['id'] if not prior or prior['protocol_hash'] == current_hash
                                else PROTOCOL['id'] + ':' + current_hash[:16])

    def register(self, now):
        if get_settings().model_feedback_horizon_days != PROTOCOL['horizon_sessions']:
            raise ValueError('Research protocol requires the canonical five-session feedback horizon')
        with model_store_connection(self.path) as conn:
            conn.execute('INSERT OR IGNORE INTO research_protocols VALUES (?,?,?,?)',
                         (self.protocol_id, protocol_hash(), json.dumps(effective_protocol(), sort_keys=True), utc_time(now).isoformat()))
            saved = conn.execute('SELECT * FROM research_protocols WHERE protocol_id=?', (self.protocol_id,)).fetchone()
        if saved['protocol_hash'] != protocol_hash():
            raise ValueError('Frozen research protocol changed; create a new experiment ID, never rewrite an existing experiment')
        # A software/feature contract change creates a new immutable generation,
        # never rewrites an in-flight test. Old evidence/artifacts are retained.
        with model_store_connection(self.path) as conn:
            superseded = conn.execute("SELECT r.* FROM research_model_rounds r JOIN research_protocols p ON p.protocol_id=r.protocol_id WHERE p.protocol_id LIKE ? AND p.protocol_hash!=? AND r.status IN ('training','failed','locked')",
                (PROTOCOL['id'] + '%', protocol_hash())).fetchall()
        for run in superseded:
            self.retire(dict(run), 'superseded_contract')

    def capture(self, ticker, market, *, now=None, loader=build_feature_dataset):
        explicit_time = now is not None
        now = utc_time(now or datetime.now(UTC))
        self.register(now)
        if ticker not in PROTOCOL['cohort'][market]:
            raise ValueError('Ticker outside frozen research cohort')
        local = now.astimezone(ZoneInfo(MARKET_CONFIGS[market].timezone))
        # Capture only a completed current-day candle. Do not backdate a late
        # snapshot to yesterday or use an incomplete intraday close as a label.
        if local.weekday() >= 5 or local.hour < 17:
            return False
        day = local.date().isoformat()
        with model_store_connection(self.path) as conn:
            existing = conn.execute('SELECT * FROM research_feature_snapshots WHERE protocol_id=? AND market=? AND ticker=? AND decision_date=?',
                (self.protocol_id, market, ticker, day)).fetchone()
        if existing:
            self._record_label(dict(existing))  # recover a crash between snapshot and feedback writes
            return False
        with model_store_connection(self.path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            attempted = conn.execute('SELECT next_attempt_utc FROM research_capture_attempts WHERE protocol_id=? AND market=? AND ticker=? AND day=?',
                (self.protocol_id, market, ticker, day)).fetchone()
            if attempted and attempted[0] > now.isoformat():
                return False
            conn.execute('INSERT OR REPLACE INTO research_capture_attempts VALUES (?,?,?,?,?)',
                (self.protocol_id, market, ticker, day, (now + timedelta(hours=1)).isoformat()))
        data = loader(ticker=ticker, period='2y', benchmark=PROTOCOL['benchmark'][market],
                      include_news_sentiment=False, market=market).sort_values('date')
        data = data[pd.to_datetime(data['date']).dt.date <= local.date()]
        if data.empty or pd.Timestamp(data.iloc[-1]['date']).date() != local.date():
            return False
        price = float(data.iloc[-1]['close'])
        if not math.isfinite(price) or price <= 0:
            return False
        technical = prepare_stationary_feature_dataset(data).iloc[-1]
        values = {}
        for key, value in technical.items():
            if (key in FEATURE_EXCLUDE_COLUMNS or key.startswith('target_') or 'sentiment' in key
                or key in ('article_count', 'positive_article_ratio', 'negative_article_ratio')):
                continue
            if isinstance(value, (int, float, np.number)):
                values[key] = float(value) if math.isfinite(float(value)) else None
        # Availability cannot precede the download/feature computation. Explicit
        # clocks are used by deterministic tests only.
        if not explicit_time:
            now = datetime.now(UTC)
            if now.astimezone(ZoneInfo(MARKET_CONFIGS[market].timezone)).date() != local.date():
                return False
        external, observation_id = asof_external_features(self.context, ticker, market, now)
        with model_store_connection(self.path) as conn:
            conn.execute('INSERT OR IGNORE INTO research_feature_snapshots VALUES (?,?,?,?,?,?,?,?,?)',
                (self.protocol_id, market, ticker, day, now.isoformat(), price,
                 json.dumps(values), json.dumps(external), observation_id))
            row = dict(conn.execute('SELECT * FROM research_feature_snapshots WHERE protocol_id=? AND market=? AND ticker=? AND decision_date=?',
                (self.protocol_id, market, ticker, day)).fetchone())
        self._record_label(row)
        return True

    def _record_label(self, row):
        self._record_prediction(row, f"{self.protocol_id}:labels:{row['market']}", 'baseline_cash', 0.0)

    def _record_prediction(self, row, version, model_name, prediction):
        return self.feedback.record_decision({
            'user_id': 'research-only', 'market': row['market'], 'ticker': row['ticker'],
            'timestamp': row['available_at_utc'], 'action': 'no_action', 'quantity': 0,
            'price': row['price'], 'model_name': model_name,
            'metadata': {'price_date': row['decision_date'], 'model_period': '2y',
                'model_ticker': 'GLOBAL', 'model_version': version, 'task_type': 'regression',
                'decision_source': 'research_shadow', 'prediction_value': float(prediction),
                'decision_reason_metadata': {'estimated_transaction_cost_pct': PROTOCOL['round_trip_cost_pct'][row['market']]}}},
            benchmark=PROTOCOL['benchmark'][row['market']])

    def training_panel(self, market):
        with model_store_connection(self.path) as conn:
            rows = conn.execute('''SELECT s.*, f.actual_return_pct,f.outcome_date FROM research_feature_snapshots s
                JOIN model_decision_feedback f ON f.market=s.market AND f.ticker=s.ticker AND f.decision_date=s.decision_date
                WHERE s.protocol_id=? AND s.market=? AND f.model_version=? AND f.status='evaluated'
                  AND substr(s.available_at_utc,1,10)<f.outcome_date
                ORDER BY s.decision_date,s.ticker''', (self.protocol_id, market, f"{self.protocol_id}:labels:{market}")).fetchall()
        return pd.DataFrame([{'date': r['decision_date'], 'ticker': r['ticker'],
            **json.loads(r['technical_json']), **json.loads(r['context_json']),
            'target_5d_return': r['actual_return_pct']} for r in rows])

    def readiness(self, market):
        panel = self.training_panel(market)
        if panel.empty:
            return {'ready': False, 'reason': 'no_matured_prospective_panel', 'dates': 0, 'selected_features': []}, panel
        dates = sorted(panel.date.unique())[-PROTOCOL['minimum_training_dates']:]
        panel = panel[panel.date.isin(dates)].copy()
        coverage = {ticker: int(panel[panel.ticker == ticker].date.nunique()) for ticker in PROTOCOL['cohort'][market]}
        selected = []
        feature_coverage = {}
        for name in PROTOCOL['external_candidates']:
            key = 'context_' + name
            values = panel.get(key, pd.Series(np.nan, index=panel.index))
            feature_coverage[name] = {'coverage': float(values.notna().mean()), 'distinct_values': int(values.nunique()),
                'ticker_coverage': {ticker: float(values[panel.ticker == ticker].notna().mean()) if (panel.ticker == ticker).any() else 0.0 for ticker in coverage}}
            if (values.notna().mean() >= PROTOCOL['minimum_feature_coverage'] and values.nunique() >= 3
                and all(values[panel.ticker == ticker].notna().mean() >= PROTOCOL['minimum_ticker_feature_coverage'] for ticker in coverage)):
                selected.append(key)
        ready = len(dates) >= PROTOCOL['minimum_training_dates'] and min(coverage.values()) >= PROTOCOL['minimum_dates_per_ticker'] and bool(selected)
        reason = ('not_enough_matured_dates' if len(dates) < PROTOCOL['minimum_training_dates'] else
                  'cohort_coverage_incomplete' if min(coverage.values()) < PROTOCOL['minimum_dates_per_ticker'] else
                  'insufficient_external_feature_coverage' if not selected else 'ready')
        return {'ready': ready, 'reason': reason, 'feature_coverage': feature_coverage,
                'dates': len(dates), 'ticker_dates': coverage, 'selected_features': selected[:PROTOCOL['maximum_external_features']]}, panel

    def advance(self, market, now=None):
        now = utc_time(now or datetime.now(UTC))
        self.register(now)
        with model_store_connection(self.path) as conn:
            row = conn.execute('SELECT * FROM research_model_rounds WHERE protocol_id=? AND market=? ORDER BY round_number DESC LIMIT 1',
                (self.protocol_id, market)).fetchone()
        if row and row['status'] not in ('retired', 'released'):
            age = now - utc_time(row['started_at_utc'])
            expired = ((row['status'] in ('training', 'failed') and age > timedelta(days=7))
                       or (row['status'] == 'locked' and age > timedelta(days=365)))
            if expired:
                self.retire(dict(row), 'training_expired' if row['status'] != 'locked' else 'forward_window_expired')
                return
        if row and row['status'] == 'locked':
            self.predict_round(dict(row), now)
            self.record_monitoring_checkpoint(dict(row))
            self.release_if_complete(dict(row))
            return
        if row and row['status'] in ('training', 'failed') and utc_time(row['attempt_at_utc']) + timedelta(hours=6) > now:
            return
        readiness, panel = self.readiness(market)
        if not readiness['ready']:
            return
        selected = readiness['selected_features']
        # Snapshot the exact same rows/labels for both arms. Feature admission is
        # deterministic coverage/variation only, never selection by test outcomes.
        panel = panel.sort_values(['date', 'ticker']).reset_index(drop=True)
        dataset_hash = frame_fingerprint(panel, effective_protocol())
        with model_store_connection(self.path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            latest = conn.execute('SELECT * FROM research_model_rounds WHERE protocol_id=? AND market=? ORDER BY round_number DESC LIMIT 1',
                (self.protocol_id, market)).fetchone()
            if latest and latest['status'] not in ('released', 'retired'):
                if utc_time(latest['attempt_at_utc']) + timedelta(hours=6) > now:
                    return
                number = latest['round_number']
                panel = pd.read_json(StringIO(latest['data_json']), convert_dates=False)
                selected = json.loads(latest['external_features_json'])
                dataset_hash = latest['dataset_hash']
                conn.execute("UPDATE research_model_rounds SET status='training',attempt_at_utc=? WHERE protocol_id=? AND market=? AND round_number=?",
                    (now.isoformat(), self.protocol_id, market, number))
            else:
                if latest and latest['dataset_hash'] == dataset_hash:
                    return
                if latest:
                    previous = pd.read_json(StringIO(latest['data_json']), convert_dates=False)
                    if 'date' in previous and sum(day > str(previous.date.max()) for day in panel.date.unique()) < 5:
                        return
                number = (latest['round_number'] if latest else 0) + 1
                conn.execute('''INSERT INTO research_model_rounds
                    (protocol_id,market,round_number,status,started_at_utc,attempt_at_utc,dataset_hash,data_json,external_features_json)
                    VALUES (?,?,?,'training',?,?,?,?,?)''',
                    (self.protocol_id, market, number, now.isoformat(), now.isoformat(), dataset_hash, panel.to_json(), json.dumps(selected)))
            claimed = dict(conn.execute('SELECT * FROM research_model_rounds WHERE protocol_id=? AND market=? AND round_number=?',
                (self.protocol_id, market, number)).fetchone())
        versions = json.loads(claimed['versions_json'])
        lifecycle = ModelLifecycleService(str(self.path))
        try:
            for arm in PROTOCOL['arms']:
                if arm in versions:
                    continue
                excluded = [c for c in panel if c.startswith('context_') and (arm == 'price_only' or c not in selected)]
                contract = {'protocol_id': self.protocol_id, 'protocol_hash': protocol_hash(), 'arm': arm,
                            'round': number, 'dataset_hash': dataset_hash, 'external_features': selected,
                            'target_price_source': 'raw_close', 'training_tickers': PROTOCOL['cohort'][market]}
                result = train_baseline_model(dataset_df=panel.drop(columns=excluded), ticker='GLOBAL', period='2y',
                    target_name=PROTOCOL['target'], task_type='regression', model_name=PROTOCOL['model'], market=market,
                    publish_canonical=False, research_contract=contract)
                registered = lifecycle.register_training_result(result, 'prospective_research')
                versions[arm] = registered['model_version']
                with model_store_connection(self.path) as conn:
                    conn.execute('UPDATE research_model_rounds SET versions_json=? WHERE protocol_id=? AND market=? AND round_number=?',
                        (json.dumps(versions), self.protocol_id, market, number))
            with model_store_connection(self.path) as conn:
                conn.execute("UPDATE research_model_rounds SET status='locked',started_at_utc=? WHERE protocol_id=? AND market=? AND round_number=?",
                    (datetime.now(UTC).isoformat(), self.protocol_id, market, number))
        except Exception:
            with model_store_connection(self.path) as conn:
                conn.execute("UPDATE research_model_rounds SET status='failed' WHERE protocol_id=? AND market=? AND round_number=?",
                    (self.protocol_id, market, number))
            raise

    def retire(self, run, reason):
        # Preserve artifacts and evidence, never touch an active deployment.
        with model_store_connection(self.path) as conn:
            conn.execute("UPDATE research_model_rounds SET status='retired',result_json=? WHERE protocol_id=? AND market=? AND round_number=?",
                (json.dumps({'reason': reason, 'inconclusive': True, 'automatic_promotion': False}), run.get('protocol_id', self.protocol_id), run['market'], run['round_number']))
            for version in json.loads(run['versions_json']).values():
                conn.execute("UPDATE model_versions SET lifecycle_status='retired' WHERE model_version=? AND lifecycle_status='research_shadow'",
                             (version,))
                conn.execute("UPDATE model_decision_feedback SET status='retired' WHERE model_version=? AND decision_source='research_shadow' AND status='pending'", (version,))
            if reason == 'superseded_contract':
                label_version = f"{run.get('protocol_id', self.protocol_id)}:labels:{run['market']}"
                conn.execute("UPDATE model_decision_feedback SET status='retired' WHERE model_version=? AND decision_source='research_shadow' AND status='pending'", (label_version,))

    def record_monitoring_checkpoint(self, run):
        # Descriptive, predeclared 20-date looks. Never used by fitting or promotion.
        versions = json.loads(run['versions_json'])
        with model_store_connection(self.path) as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM model_decision_feedback WHERE model_version IN (?,?) AND status='evaluated'",
                    tuple(versions[a] for a in PROTOCOL['arms']))]
        arms = {a: {(r['ticker'], r['decision_date']): r for r in rows if r['model_version'] == v} for a, v in versions.items()}
        common = arms['price_only'].keys() & arms['price_context'].keys()
        dates = sorted({k[1] for k in common})
        count = len(dates) // 20 * 20
        if count < 20 or count >= PROTOCOL['locked_test_dates']:
            return
        with model_store_connection(self.path) as conn:
            if conn.execute('SELECT 1 FROM research_monitoring_checkpoints WHERE protocol_id=? AND market=? AND round_number=? AND dates=?',
                (self.protocol_id, run['market'], run['round_number'], count)).fetchone():
                return
            snapshots = conn.execute('SELECT ticker,decision_date,technical_json FROM research_feature_snapshots WHERE protocol_id=? AND market=?',
                                    (self.protocol_id, run['market'])).fetchall()
        keys = {k for k in common if k[1] in dates[:count]}
        if any(arms['price_only'][k]['actual_return_pct'] != arms['price_context'][k]['actual_return_pct'] for k in keys):
            return
        momentum = {(r['ticker'], r['decision_date']): json.loads(r['technical_json']).get('return_20d_pct', 0) for r in snapshots}
        result = evaluate_matched_research({a: {k: rs[k] for k in keys} for a, rs in arms.items()}, run['market'], momentum)
        result.update(interim_only=True, locked_test_released=False, eligible=False,
                      warning='Descriptive monitoring; repeated looks are not a success test. Models and gates remain frozen.')
        with model_store_connection(self.path) as conn:
            conn.execute('INSERT OR IGNORE INTO research_monitoring_checkpoints VALUES (?,?,?,?,?)',
                (self.protocol_id, run['market'], run['round_number'], count, json.dumps(result, allow_nan=False)))

    def predict_round(self, run, now):
        versions = json.loads(run['versions_json'])
        local_day = now.astimezone(ZoneInfo(MARKET_CONFIGS[run['market']].timezone)).date().isoformat()
        with model_store_connection(self.path) as conn:
            rows = conn.execute('''SELECT * FROM research_feature_snapshots WHERE protocol_id=? AND market=?
                AND available_at_utc>=? AND decision_date=?''', (self.protocol_id, run['market'], run['started_at_utc'], local_day)).fetchall()
            prior = conn.execute('SELECT DISTINCT decision_date FROM model_decision_feedback WHERE model_version IN (?,?) ORDER BY decision_date',
                tuple(versions[arm] for arm in PROTOCOL['arms'])).fetchall()
        prior_dates = {r[0] for r in prior}
        if len(prior_dates) >= PROTOCOL['locked_test_dates'] and local_day not in prior_dates:
            return
        version_store = ModelLifecycleService(str(self.path)).version_service
        bundles = {}
        for arm, version in versions.items():
            registered = version_store.get_version(version)
            bundles[arm] = load_trained_model_bundle(ticker='GLOBAL', market=run['market'], period='2y',
                target_name=PROTOCOL['target'], model_name=PROTOCOL['model'], artifact_dir=registered['artifact_dir'])
        for row in rows:
            values = {**json.loads(row['technical_json']), **json.loads(row['context_json'])}
            predictions = {}
            for arm, bundle in bundles.items():
                frame = pd.DataFrame([{key: values.get(key) for key in bundle['feature_names']}], dtype=float)
                prediction = float(bundle['model'].predict(frame)[0])
                if not math.isfinite(prediction):
                    raise ValueError('Nonfinite research prediction')
                predictions[arm] = prediction
            for arm, prediction in predictions.items():
                self._record_prediction(dict(row), versions[arm], PROTOCOL['model'], prediction)

    def release_if_complete(self, run):
        versions = json.loads(run['versions_json'])
        with model_store_connection(self.path) as conn:
            rows = conn.execute('SELECT * FROM model_decision_feedback WHERE model_version IN (?,?) ORDER BY decision_date,ticker',
                tuple(versions[arm] for arm in PROTOCOL['arms'])).fetchall()
            snapshots = conn.execute('SELECT ticker,decision_date,technical_json FROM research_feature_snapshots WHERE protocol_id=? AND market=?',
                (self.protocol_id, run['market'])).fetchall()
        grouped = {arm: {(r['ticker'], r['decision_date']): dict(r) for r in rows if r['model_version'] == version} for arm, version in versions.items()}
        common = grouped['price_only'].keys() & grouped['price_context'].keys()
        # A partial pair or pending outcome keeps the report sealed.
        if (len({r['decision_date'] for r in rows}) < PROTOCOL['locked_test_dates']
            or any(r['status'] != 'evaluated' for r in rows)):
            return False
        invalid = []
        if len(common) * 2 != len(rows):
            invalid.append('incomplete_forward_pairs')
        if any(sum(k[0] == ticker for k in common) < PROTOCOL['minimum_forward_dates_per_ticker']
               for ticker in PROTOCOL['cohort'][run['market']]):
            invalid.append('insufficient_forward_cohort_coverage')
        for key in common:
            left, right = grouped['price_only'][key], grouped['price_context'][key]
            if (left['outcome_date'] != right['outcome_date'] or
                any(not math.isclose(float(left[field]), float(right[field]), rel_tol=1e-8, abs_tol=1e-8)
                    for field in ('decision_price', 'actual_return_pct', 'benchmark_return_pct'))):
                invalid.append('inconsistent_pair_prices_or_outcomes')
                break
        momentum = {(r['ticker'], r['decision_date']): json.loads(r['technical_json']).get('return_20d_pct', 0) for r in snapshots}
        result = ({'inconclusive': True, 'reasons': invalid, 'automatic_promotion': False,
                   'locked_test_released': True} if invalid else evaluate_matched_research(grouped, run['market'], momentum))
        with model_store_connection(self.path) as conn:
            conn.execute("UPDATE research_model_rounds SET status='released',result_json=? WHERE protocol_id=? AND market=? AND round_number=?",
                (json.dumps(result, allow_nan=False), self.protocol_id, run['market'], run['round_number']))
        return True

    def status(self):
        with model_store_connection(self.path) as conn:
            rounds = conn.execute('SELECT * FROM research_model_rounds WHERE protocol_id=? ORDER BY market,round_number DESC', (self.protocol_id,)).fetchall()
            registered = conn.execute('SELECT protocol_hash,started_at_utc FROM research_protocols WHERE protocol_id=?', (self.protocol_id,)).fetchone()
            previous_protocols = [dict(r) for r in conn.execute('SELECT protocol_id,started_at_utc FROM research_protocols WHERE protocol_id!=? ORDER BY started_at_utc DESC', (self.protocol_id,))]
        progress = {}
        for market in PROTOCOL['cohort']:
            ready, panel = self.readiness(market)
            latest = next((r for r in rounds if r['market'] == market), None)
            fingerprint = frame_fingerprint(panel.sort_values(['date', 'ticker']).reset_index(drop=True), effective_protocol()) if not panel.empty else None
            with model_store_connection(self.path) as conn:
                snapshot = conn.execute('SELECT COUNT(*) AS observations,COUNT(DISTINCT decision_date) AS dates,MAX(available_at_utc) AS latest FROM research_feature_snapshots WHERE protocol_id=? AND market=?', (self.protocol_id, market)).fetchone()
            progress[market] = {**ready, 'snapshot_counts': dict(snapshot), 'required_training_dates': PROTOCOL['minimum_training_dates'],
                               'dataset_fingerprint': fingerprint, 'new_information': bool(fingerprint and (not latest or latest['dataset_hash'] != fingerprint)),
                               'waiting_reason': 'forward_test_in_progress' if latest and latest['status'] == 'locked' else ready['reason']}
            added = len(panel.date.unique()) if not panel.empty else 0
            if latest:
                previous = pd.read_json(StringIO(latest['data_json']), convert_dates=False)
                added = sum(day > str(previous.date.max()) for day in panel.date.unique()) if 'date' in previous and not panel.empty else 0
            eligible = ready['ready'] and (not latest or (latest['status'] in ('released', 'retired') and added >= 5 and latest['dataset_hash'] != fingerprint))
            progress[market].update(new_matured_dates=added, next_training_eligible=bool(eligible))
            if latest and latest['status'] in ('released', 'retired') and not eligible:
                progress[market]['waiting_reason'] = 'waiting_for_five_new_matured_dates' if added < 5 else ready['reason']
            if latest and latest['status'] in ('training', 'failed'):
                progress[market]['waiting_reason'] = 'training_or_retry_backoff'
                progress[market]['retry_after_utc'] = (utc_time(latest['attempt_at_utc']) + timedelta(hours=6)).isoformat()
        output = []
        for row in rounds[:20]:
            versions = json.loads(row['versions_json'])
            with model_store_connection(self.path) as conn:
                counts = [dict(r) for r in conn.execute('SELECT model_version,COUNT(*) AS observations,COUNT(DISTINCT decision_date) AS prediction_dates,SUM(status=\'evaluated\') AS matured FROM model_decision_feedback WHERE model_version IN (?,?) GROUP BY model_version',
                         tuple(versions.get(a, '') for a in PROTOCOL['arms']))]
                checkpoint = conn.execute('SELECT dates,result_json FROM research_monitoring_checkpoints WHERE protocol_id=? AND market=? AND round_number=? ORDER BY dates DESC LIMIT 1',
                    (self.protocol_id, row['market'], row['round_number'])).fetchone()
            output.append({k: row[k] for k in ('market', 'round_number', 'status', 'started_at_utc', 'dataset_hash')})
            output[-1].update(arms={a: next((c for c in counts if c['model_version'] == v), {'observations': 0, 'prediction_dates': 0, 'matured': 0}) for a, v in versions.items()},
                required_forward_dates=PROTOCOL['locked_test_dates'], result_json=json.loads(row['result_json']) if row['status'] in ('released', 'retired') and row['result_json'] else None,
                monitoring={'dates': checkpoint['dates'], 'result': json.loads(checkpoint['result_json'])} if checkpoint else None)
        return {'protocol_id': self.protocol_id, 'protocol_hash': protocol_hash(), 'production_enabled': False,
            'previous_protocols': previous_protocols,
            'registered_protocol': dict(registered) if registered else None,
            'protocol_matches': registered['protocol_hash'] == protocol_hash() if registered else None,
            'readiness': progress, 'rounds': output}


def evaluate_matched_research(arms, market, momentum):
    """Released or provisional opportunity diagnostics, never account P&L."""
    from sklearn.metrics import balanced_accuracy_score
    from app.services.paired_model_evidence import compare_paired_rows
    keys = sorted(arms['price_only'].keys() & arms['price_context'].keys(), key=lambda k: (k[1], k[0]))
    actual = np.array([arms['price_only'][k]['actual_return_pct'] for k in keys], float)
    cost = PROTOCOL['round_trip_cost_pct'][market]
    output = {}
    for arm in PROTOCOL['arms'] + PROTOCOL['baselines']:
        if arm in arms:
            prediction = np.array([arms[arm][k]['prediction_value'] for k in keys], float)
            returns = actual
        elif arm == 'momentum_20d':
            prediction = np.array([2.0 if float(momentum.get(k) or 0) > 0 else 0.0 for k in keys])
            returns = actual
        else:
            prediction = np.full(len(keys), 0 if arm == 'cash' else 2.0)
            returns = np.array([arms['price_only'][k]['benchmark_return_pct'] for k in keys], float) if arm == 'benchmark' else actual
        net = np.where(prediction > PROTOCOL['buy_threshold_pct'], returns - cost, 0)
        daily = pd.Series(net).groupby([k[1] for k in keys]).mean()
        paths = []
        for phase in range(6):
            previous_end = ''
            values = []
            for day in list(daily.index)[phase:]:
                if day <= previous_end:
                    continue
                previous_end = max(arms['price_only'][k]['outcome_date'] for k in keys if k[1] == day)
                values.append(float(daily[day]))
            paths.append({'blocks': len(values), 'mean_net_return_pct': float(np.mean(values)) if values else None})
        output[arm] = {'rows': len(keys), 'direction_accuracy': float(np.mean((prediction > 0) == (actual > 0))),
            'balanced_accuracy': float(balanced_accuracy_score(actual > 0, prediction > 0)) if len(set(actual > 0)) == 2 else None,
            'mae_pct': float(np.mean(abs(prediction - actual))) if arm in arms else None,
            'brier_score': None, 'probability_note': 'return regressors do not issue calibrated class probabilities',
            'net_return_per_opportunity_pct': float(np.mean(net)),
            'nonoverlapping_path_returns': paths}
    paired = compare_paired_rows(list(arms['price_context'].values()), list(arms['price_only'].values()))
    return {'matched_rows': len(keys), 'metrics': output, 'is_portfolio_profit': False,
            'incremental_direction_accuracy': output['price_context']['direction_accuracy'] - output['price_only']['direction_accuracy'],
            'incremental_net_return_pct': output['price_context']['net_return_per_opportunity_pct'] - output['price_only']['net_return_per_opportunity_pct'],
            'paired_evidence': paired, 'automatic_promotion': False, 'locked_test_released': True}


_CONTRACT_FUNCTIONS = (asof_external_features, ContextObservationStore.record, ContextObservationStore.as_of,
                       ProspectiveModelResearch.capture, ProspectiveModelResearch._record_prediction,
                       ProspectiveModelResearch.training_panel, evaluate_matched_research)


def run_prospective_research_cycle(now=None):
    if not get_settings().prospective_research_enabled:
        return {'enabled': False}
    if not (get_settings().context_archive_enabled and get_settings().external_context_enabled
            and get_settings().model_feedback_enabled):
        return {'enabled': True, 'status': 'waiting_for_archive_context_and_feedback_to_be_enabled'}
    import logging
    from app.services.context_collection_service import collect_symbol
    from app.services.learning_operations import LearningOperations
    service = ProspectiveModelResearch()
    ops = LearningOperations()
    ops.heartbeat('research', 'running')
    failed = False
    now = utc_time(now or datetime.now(UTC))
    for market, cohort in PROTOCOL['cohort'].items():
        try:
            # Research snapshots are once/day; cached context is reused where fresh.
            local = now.astimezone(ZoneInfo(MARKET_CONFIGS[market].timezone))
            if local.weekday() < 5 and local.hour >= 17:
                for ticker in cohort:
                    with model_store_connection(service.path) as conn:
                        exists = conn.execute('SELECT 1 FROM research_feature_snapshots WHERE protocol_id=? AND market=? AND ticker=? AND decision_date=?',
                            (service.protocol_id, market, ticker, local.date().isoformat())).fetchone()
                    try:
                        if not exists:
                            collect_symbol(ticker, market, now)
                        service.capture(ticker, market)
                    except Exception:
                        failed = True
                        logging.getLogger(__name__).exception('Prospective capture failed market=%s ticker=%s', market, ticker)
            service.advance(market, now)
        except Exception:
            failed = True
            logging.getLogger(__name__).exception('Prospective research cycle failed market=%s', market)
    ops.heartbeat('research', 'failed' if failed else 'completed')
    return service.status()
