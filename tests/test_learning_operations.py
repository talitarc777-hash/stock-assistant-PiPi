"""Operational correctness only; synthetic results are not predictive evidence."""
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock
from uuid import uuid4

import pandas as pd

from app.core.settings import Settings
from app.core.sqlite_store import model_store_connection
from app.services.learning_operations import LearningOperations
from app.services.context_observation_store import ContextObservationStore
from app.services.prospective_model_research import ProspectiveModelResearch, PROTOCOL
from app.services.external_market_context_service import (
    _entity_matches, _empty_context, _add_yfinance_news_context,
    _append_note, source_statuses, _add_alpha_vantage_context, _add_sec_context,
)


class LearningOperationsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path('data') / ('test_learning_ops_' + uuid4().hex)
        self.root.mkdir()
        self.path = self.root / 'state.db'
        self.ops = LearningOperations(self.path)
        self.now = datetime(2026, 9, 28, 10, tzinfo=UTC)

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_durable_claim_backoff_and_success_reset(self):
        self.assertTrue(self.ops.claim('HK', '0700', self.now))
        restarted = LearningOperations(self.path)
        self.assertFalse(restarted.claim('HK', '0700', self.now))
        self.ops.finish('HK', '0700', False, {}, self.now)
        self.assertFalse(restarted.claim('HK', '0700', self.now + timedelta(minutes=14)))
        later = self.now + timedelta(minutes=15)
        self.assertTrue(restarted.claim('HK', '0700', later))
        restarted.finish('HK', '0700', True, {'yfinance_news_lexicon_v1': 'usable', 'secret': 'token'}, later)
        with model_store_connection(self.path) as conn:
            row = dict(conn.execute('SELECT * FROM learning_collection_jobs').fetchone())
        self.assertEqual(row['failures'], 0)
        self.assertNotIn('token', json.dumps(row))
        self.assertFalse(restarted.claim('HK', '0700', later + timedelta(minutes=59)))

    def test_daily_budget_survives_restart_and_resets_next_day(self):
        for _ in range(2):
            self.assertTrue(self.ops.spend_budget('alpha', 2, self.now))
        self.assertFalse(LearningOperations(self.path).spend_budget('alpha', 2, self.now))
        self.assertTrue(self.ops.spend_budget('alpha', 2, self.now + timedelta(days=1)))

    def test_provider_success_without_archive_is_not_learning_success(self):
        from app.services.context_collection_service import collect_symbol
        with patch('app.services.context_collection_service.build_external_market_context', return_value={'news_sentiment_score': .5}):
            result = collect_symbol('AAPL', 'US', self.now, db_path=self.path)
        self.assertFalse(result['usable'])
        self.assertFalse(result['recorded'])
        with model_store_connection(self.path) as conn:
            row = conn.execute('SELECT * FROM learning_collection_jobs').fetchone()
        self.assertEqual(row['status'], 'no_usable_context')

    def test_missing_current_close_retries_at_most_hourly(self):
        service = ProspectiveModelResearch(self.path)
        loader = MagicMock(return_value=pd.DataFrame({'date': ['2026-09-25'], 'close': [100.]}))
        self.assertFalse(service.capture('0700', 'HK', now=self.now, loader=loader))
        self.assertFalse(service.capture('0700', 'HK', now=self.now + timedelta(minutes=15), loader=loader))
        self.assertEqual(loader.call_count, 1)
        self.assertFalse(service.capture('0700', 'HK', now=self.now + timedelta(hours=1), loader=loader))
        self.assertEqual(loader.call_count, 2)

    def test_entity_matching_rejects_ambiguous_numeric_or_short_symbols(self):
        self.assertFalse(_entity_matches('700 people bought phones', '0700.HK'))
        self.assertFalse(_entity_matches('A new product', 'A'))
        self.assertTrue(_entity_matches('$A earnings', 'A'))
        self.assertTrue(_entity_matches('Tencent reports earnings', '0700.HK', 'Tencent Holdings Limited'))
        self.assertTrue(_entity_matches('0700.HK reports earnings', '0700.HK'))
        context = _empty_context('0700.HK')
        provider = SimpleNamespace(news=[{'title': 'Good earnings', 'relatedTickers': ['AAPL']},
                                        {'title': 'Strong profit', 'relatedTickers': ['0700.HK']}])
        with patch('app.services.external_market_context_service.yf.Ticker', return_value=provider):
            _add_yfinance_news_context(context, ticker='0700.HK')
        self.assertEqual(context['news_article_count'], 1)

    def test_credentials_and_hk_unsupported_are_explicit_and_safe(self):
        settings = Settings(alpha_vantage_api_key=None, sec_user_agent='contact@example.com')
        with patch('app.services.external_market_context_service.get_settings', return_value=settings), \
             patch('app.services.external_market_context_service._fetch_json') as fetch:
            context = _empty_context('AAPL')
            _add_alpha_vantage_context(context, ticker='AAPL', timeout=1)
            _add_sec_context(context, ticker='AAPL', timeout=1)
            fetch.assert_not_called()
            states = source_statuses(context)
            self.assertEqual(states['sec_edgar_filings'], 'missing_credentials')
            self.assertEqual(states['alpha_vantage_news_sentiment'], 'missing_credentials')
            self.assertEqual(source_statuses(_empty_context('0700.HK'))['sec_edgar_filings'], 'unsupported_market')
            _append_note(context, 'Provider failed: https://provider?apikey=SECRET')
            self.assertNotIn('SECRET', json.dumps(context))

    def test_collector_scales_bounded_batch_and_deduplicates(self):
        from app.services.context_collection_service import collect_due_context
        settings = Settings(prospective_research_enabled=False, context_archive_enabled=True, context_collection_batch_max=8)
        store = ContextObservationStore(str(self.path))
        profiles = MagicMock()
        profiles.list_effective_watchlist_tickers.return_value = []
        with patch('app.services.context_collection_service.get_settings', return_value=settings), \
             patch('app.services.context_collection_service.get_context_observation_store', return_value=store), \
             patch('app.services.context_collection_service.get_user_profile_store', return_value=profiles), \
             patch('app.services.context_collection_service.get_active_universe', return_value=[f'T{i}' for i in range(120)]), \
             patch('app.services.context_collection_service.build_external_market_context', return_value={'observation_id': 1, 'news_sentiment_score': .2}) as provider:
            self.assertEqual(collect_due_context(self.now)['attempted'], 5)
            self.assertEqual(collect_due_context(self.now)['attempted'], 0)
            self.assertEqual(provider.call_count, 5)

    def test_failed_round_retires_without_retraining_identical_data(self):
        service = ProspectiveModelResearch(self.path)
        service.register(self.now)
        old = (self.now - timedelta(days=8)).isoformat()
        with model_store_connection(self.path) as conn:
            conn.execute('INSERT INTO research_model_rounds VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                         (PROTOCOL['id'], 'HK', 1, 'failed', old, old, 'hash', '{}', '[]', '{}', None))
        with patch('app.services.prospective_model_research.train_baseline_model') as fit:
            service.advance('HK', self.now)
            service.advance('HK', self.now)
            fit.assert_not_called()
        row = service.status()['rounds'][0]
        self.assertEqual(row['status'], 'retired')
        self.assertEqual(row['result_json']['reason'], 'training_expired')
        self.assertIsInstance(json.loads(json.dumps(service.status()))['previous_protocols'], list)

    def test_contract_upgrade_starts_new_generation_without_overwriting_old_evidence(self):
        service = ProspectiveModelResearch(self.path)
        service.register(self.now)
        old_id = service.protocol_id
        with model_store_connection(self.path) as conn:
            conn.execute('INSERT INTO research_model_rounds VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                         (old_id, 'HK', 1, 'locked', self.now.isoformat(), self.now.isoformat(), 'hash', '{}', '[]', '{}', None))
        with patch.dict(PROTOCOL, {'version': 2}):
            upgraded = ProspectiveModelResearch(self.path)
            upgraded.register(self.now)
            self.assertNotEqual(upgraded.protocol_id, old_id)
        with model_store_connection(self.path) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM research_protocols').fetchone()[0], 2)
            row = conn.execute('SELECT * FROM research_model_rounds WHERE protocol_id=?', (old_id,)).fetchone()
            self.assertEqual(row['status'], 'retired')
            self.assertEqual(row['dataset_hash'], 'hash')

    def test_interim_monitoring_is_descriptive_and_does_not_release_or_promote(self):
        service = ProspectiveModelResearch(self.path)
        versions = {'price_only': 'p', 'price_context': 'c'}
        run = {'market': 'HK', 'versions_json': json.dumps(versions), 'round_number': 1}
        for date in pd.bdate_range('2025-01-01', periods=20):
            day = date.date().isoformat()
            for ticker in PROTOCOL['cohort']['HK']:
                row = dict(market='HK', ticker=ticker, decision_date=day, available_at_utc=day+'T10:00:00+00:00', price=100.)
                for version in versions.values():
                    with patch('app.services.model_feedback_service._utc_now_iso', return_value=row['available_at_utc']):
                        service._record_prediction(row, version, 'ridge_regression', 2)
            with model_store_connection(self.path) as conn:
                conn.execute("UPDATE model_decision_feedback SET status='evaluated',actual_return_pct=1,benchmark_return_pct=1,outcome_date=? WHERE decision_date=?",
                             ((date + pd.offsets.BDay(5)).date().isoformat(), day))
        service.record_monitoring_checkpoint(run)
        service.record_monitoring_checkpoint(run)
        with model_store_connection(self.path) as conn:
            rows = conn.execute('SELECT * FROM research_monitoring_checkpoints').fetchall()
        self.assertEqual(len(rows), 1)
        result = json.loads(rows[0]['result_json'])
        self.assertTrue(result['interim_only'])
        self.assertFalse(result['eligible'])
        self.assertFalse(result['locked_test_released'])
        self.assertFalse(result['automatic_promotion'])
        self.assertFalse(service.release_if_complete(run))

    def test_health_endpoint_does_not_expose_paths_tokens_or_contacts(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.model_lifecycle import router
        from app.services.learning_operations import learning_health
        settings = Settings(profile_db_path=str(self.path), alpha_vantage_api_key='SECRET', sec_user_agent='Person private@email.test')
        service = ProspectiveModelResearch(self.path)
        with patch('app.services.learning_operations.get_settings', return_value=settings), \
             patch('app.services.learning_operations.collection_universe', return_value=['AAPL']), \
             patch('app.services.context_observation_store.get_context_observation_store', return_value=service.context), \
             patch('app.services.prospective_model_research.ProspectiveModelResearch', return_value=service), \
             patch('app.services.external_market_context_service.get_settings', return_value=settings):
            payload = learning_health()
            app = FastAPI()
            app.include_router(router)
            response = TestClient(app).get('/model-lifecycle/learning-health')
        self.assertEqual(response.status_code, 200)
        encoded = json.dumps(payload)
        for private in ('SECRET', 'private@email.test', str(self.path)):
            self.assertNotIn(private, encoded)
        self.assertIn('lifecycle_scheduler_not_started', payload['blockers'])


if __name__ == '__main__':
    unittest.main()
