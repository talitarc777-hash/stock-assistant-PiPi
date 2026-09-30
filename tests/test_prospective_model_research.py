"""Synthetic correctness tests, not evidence of financial predictive performance."""
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np
import pandas as pd

from app.services.context_observation_store import ContextObservationStore, summarize_context_coverage
from app.services.prospective_model_research import PROTOCOL, ProspectiveModelResearch, asof_external_features
from app.services.training_evidence import TrainingEvidenceStore, frame_fingerprint
from app.services.model_training import train_new_baseline_model
from app.core.sqlite_store import model_store_connection
from tests import test_model_training as training_fixtures


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path('data') / f'test_prospective_{uuid4().hex}'
        self.root.mkdir()
        self.path = self.root / 'state.db'
        self.service = ProspectiveModelResearch(self.path)
        self.now = datetime(2026, 9, 23, 12, tzinfo=UTC)

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_fingerprint_changes_for_labels_features_and_contract(self):
        frame = pd.DataFrame({'x': [1., 2.], 'target': [2., 3.]})
        original = frame_fingerprint(frame, {'schema': 1})
        self.assertEqual(original, frame_fingerprint(frame.copy(), {'schema': 1}))
        self.assertNotEqual(original, frame_fingerprint(frame, {'schema': 2}))
        frame.loc[0, 'target'] = 10
        self.assertNotEqual(original, frame_fingerprint(frame, {'schema': 1}))

    def test_training_guard_restart_minimum_new_dates_and_lease(self):
        guard = TrainingEvidenceStore(self.path)
        artifact = self.root / 'artifact.pkl'
        artifact.write_bytes(b'test')
        dates = ['2026-09-01', '2026-09-02']
        self.assertTrue(guard.claim('US:AAPL', 'first', dates, now=self.now)[0])
        self.assertFalse(guard.claim('US:AAPL', 'first', dates, now=self.now)[0])
        guard.complete('US:AAPL', 'first', dates, artifact)
        restarted = TrainingEvidenceStore(self.path)
        self.assertEqual(restarted.claim('US:AAPL', 'first', dates, now=self.now)[1], 'identical_labeled_dataset')
        self.assertEqual(restarted.claim('US:AAPL', 'revision', dates, now=self.now)[1], 'insufficient_new_matured_dates')
        self.assertTrue(restarted.claim('US:AAPL', 'new', dates + [f'2026-09-{i:02d}' for i in range(3, 8)], now=self.now)[0])
        self.assertTrue(restarted.claim('HK:0700', 'first', dates, now=self.now)[0])

    def test_canonical_trainer_does_not_fit_again_on_identical_labels(self):
        frame = pd.DataFrame({'date': pd.bdate_range('2025-01-01', periods=150),
                              'ticker': 'AAPL', 'x': np.sin(np.arange(150)),
                              'target_5d_return': np.sin(np.arange(150)) * 3})
        settings = SimpleNamespace(profile_db_path=str(self.path), research_models_dir=str(self.root / 'models'))
        with patch('app.services.model_training.get_settings', return_value=settings):
            args = dict(dataset_df=frame, ticker='AAPL', period='2y', target_name='target_5d_return',
                        task_type='regression', model_name='ridge_regression', market='US',
                        publish_canonical=False, evidence_gated=True)
            first = train_new_baseline_model(**args)
            self.assertTrue(first.artifact.model_path.is_file())
            with patch('app.services.model_training.train_baseline_model') as train:
                # Completed fit before lifecycle registration: replay the exact
                # immutable artifact, do not fit it again or lose the candidate.
                recovered = train_new_baseline_model(**args)
                self.assertEqual(recovered.artifact.model_version, first.artifact.model_version)
                from app.services.model_version_service import ModelVersionService
                ModelVersionService(str(self.path)).register_training_result(result=first, market='US',
                    is_validated=False, validation_score=0, rejection_reasons=[], retrain_type='test', parent_model_version=None)
                self.assertIsNone(train_new_baseline_model(**args))
                unlabeled = pd.concat([frame, pd.DataFrame([{'date': '2027-01-01', 'ticker': 'AAPL', 'x': 5, 'target_5d_return': np.nan}])], ignore_index=True)
                self.assertIsNone(train_new_baseline_model(**{**args, 'dataset_df': unlabeled}))
                train.assert_not_called()

    def test_protocol_is_immutable_and_missing_data_waits(self):
        self.service.register(self.now)
        with patch.dict(PROTOCOL, {'ridge_alpha': 11}):
            with self.assertRaises(ValueError):
                self.service.register(self.now)
        self.assertFalse(self.service.readiness('HK')[0]['ready'])
        self.service.advance('HK', self.now)
        self.assertEqual(self.service.status()['rounds'], [])

    def test_research_is_opt_in_and_status_endpoint_is_read_only_of_models(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.model_lifecycle import router
        from app.services.prospective_model_research import run_prospective_research_cycle
        with patch('app.services.prospective_model_research.get_settings',
                   return_value=SimpleNamespace(prospective_research_enabled=False)), \
             patch('app.services.prospective_model_research.ProspectiveModelResearch') as constructor:
            self.assertEqual(run_prospective_research_cycle(), {'enabled': False})
            constructor.assert_not_called()
        app = FastAPI()
        app.include_router(router)
        with patch('app.services.prospective_model_research.ProspectiveModelResearch', return_value=self.service):
            response = TestClient(app).get('/model-lifecycle/research-status')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['production_enabled'])
        self.assertEqual(response.json()['rounds'], [])
        self.assertFalse(response.json()['readiness']['HK']['ready'])

    def test_asof_contract_and_coverage_never_backfill(self):
        context = {'fetched_at_utc': self.now.isoformat(), 'news_sentiment_score': 0.3,
                   'sources_available': ['yfinance_news_lexicon_v1']}
        self.service.context.record('0700', 'HK', context, now=self.now + timedelta(hours=1))
        self.assertIsNone(asof_external_features(self.service.context, '0700', 'HK', self.now)[1])
        values, record = asof_external_features(self.service.context, '0700', 'HK', self.now + timedelta(hours=2))
        self.assertIsNotNone(record)
        self.assertEqual(values['context_news_sentiment_score'], 0.3)
        with model_store_connection(self.path) as conn:
            rows = [dict(r) for r in conn.execute('SELECT * FROM external_context_observations')]
        quality = summarize_context_coverage(rows, self.now + timedelta(hours=2))
        self.assertEqual(quality['HK']['observations'], 1)
        self.assertEqual(quality['HK']['feature_missing_fraction']['analyst_consensus_score'], 1)
        self.assertEqual(quality['US']['observations'], 0)

    def test_capture_uses_closed_current_bar_and_excludes_future_targets(self):
        data = training_fixtures._build_synthetic_dataset(150)
        data['date'] = pd.bdate_range(end='2026-09-23', periods=150)
        data['ticker'] = '0700'
        # Even a maliciously present future target must not become a feature.
        data['target_future_leak'] = 999
        context = {'fetched_at_utc': (self.now - timedelta(minutes=1)).isoformat(), 'news_sentiment_score': 0.4}
        self.service.context.record('0700', 'HK', context, now=self.now - timedelta(minutes=1))
        self.assertTrue(self.service.capture('0700', 'HK', now=self.now, loader=lambda **_: data))
        self.assertFalse(self.service.capture('0700', 'HK', now=self.now, loader=lambda **_: self.fail('duplicate fetch')))
        with model_store_connection(self.path) as conn:
            row = conn.execute('SELECT * FROM research_feature_snapshots').fetchone()
            self.assertFalse(any(k.startswith('target_') for k in json.loads(row['technical_json'])))
            self.assertEqual(json.loads(row['context_json'])['context_news_sentiment_score'], 0.4)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM model_decision_feedback').fetchone()[0], 1)
        self.assertFalse(self.service.capture('0005', 'HK', now=self.now - timedelta(hours=5), loader=lambda **_: self.fail('intraday fetch')))
        self.assertFalse(self.service.capture('1810', 'HK', now=self.now, loader=lambda **_: data.iloc[:-1]))

    def test_research_feedback_settles_without_entering_production_summary(self):
        row = dict(market='US', ticker='AAPL', decision_date='2026-01-02', available_at_utc='2026-01-02T22:00:00+00:00', price=100.)
        self.service._record_prediction(row, 'research-v1', 'ridge_regression', 2)
        dates = pd.bdate_range('2026-01-02', periods=8)
        result = self.service.feedback.evaluate_pending(price_loader=lambda *_: pd.DataFrame({'date': dates, 'close': [100+i for i in range(8)]}))
        self.assertEqual(result['evaluated'], 1)
        with model_store_connection(self.path) as conn:
            outcome = conn.execute('SELECT actual_return_pct FROM model_decision_feedback').fetchone()[0]
            self.assertAlmostEqual(outcome, 5)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM model_prediction_horizon_outcomes').fetchone()[0], 0)
        self.assertEqual(self.service.feedback.list_feedback(), [])

    def seed_panel(self, market='US', periods=125):
        dates = pd.bdate_range('2025-01-01', periods=periods)
        for index, date in enumerate(dates):
            day = date.date().isoformat()
            for ticker in PROTOCOL['cohort'][market]:
                available = day + 'T22:00:00+00:00'
                row = {'market': market, 'ticker': ticker, 'decision_date': day, 'available_at_utc': available, 'price': 100.0}
                with model_store_connection(self.path) as conn:
                    conn.execute('INSERT INTO research_feature_snapshots VALUES (?,?,?,?,?,?,?,?,?)',
                        (PROTOCOL['id'], market, ticker, day, available, 100., json.dumps({'x': float(np.sin(index)), 'return_20d_pct': 1.}),
                         json.dumps({'context_news_sentiment_score': float(np.cos(index)), 'context_analyst_consensus_score': None}), None))
                with patch('app.services.model_feedback_service._utc_now_iso', return_value=available):
                    self.service._record_label(row)
                with model_store_connection(self.path) as conn:
                    conn.execute("UPDATE model_decision_feedback SET status='evaluated',actual_return_pct=?,benchmark_return_pct=0.5,outcome_date=? WHERE ticker=? AND decision_date=?",
                                 (float(np.cos(index) * 3), (date + pd.offsets.BDay(5)).date().isoformat(), ticker, day))

    def test_real_ridge_pair_uses_common_rows_and_cannot_activate(self):
        self.seed_panel()
        ready, panel = self.service.readiness('US')
        self.assertTrue(ready['ready'], ready)
        self.assertEqual(len(panel), 480)
        self.assertEqual(ready['selected_features'], ['context_news_sentiment_score'])
        settings = SimpleNamespace(profile_db_path=str(self.path), research_models_dir=str(self.root / 'models'))
        with patch('app.services.model_training.get_settings', return_value=settings):
            self.service.advance('US', self.now)
        from app.services.model_version_service import ModelVersionService
        versions = ModelVersionService(str(self.path))
        saved = versions.list_versions(market='US')
        self.assertEqual(len(saved), 2)
        for version in saved:
            self.assertTrue(version['metrics_summary']['experimental_only'])
            with self.assertRaises(ValueError):
                versions.activate_version(version['model_version'], reason='test')
        contracts = [v['metrics_summary']['research_contract'] for v in saved]
        self.assertEqual(contracts[0]['dataset_hash'], contracts[1]['dataset_hash'])
        self.assertEqual({c['arm'] for c in contracts}, {'price_only', 'price_context'})
        self.assertEqual(self.service.status()['rounds'][0]['status'], 'locked')
        self.assertIsNone(self.service.status()['rounds'][0]['result_json'])
        self.assertEqual(versions.list_shadow_challengers(ticker='AAPL', market='US', periods=('2y',), target_name='target_5d_return'), [])
        # Generic feedback APIs and production calibration exclude experimental labels.
        self.assertEqual(self.service.feedback.list_feedback(limit=1000), [])
        summary = self.service.feedback.get_model_summary(ticker='GLOBAL', market='US', model_period='2y', model_name='baseline_cash')
        self.assertEqual(summary['sample_count'], 0)
        with model_store_connection(self.path) as conn:
            run = dict(conn.execute('SELECT * FROM research_model_rounds').fetchone())
        # New forward input is after both frozen models exist. Re-running must
        # neither refit nor generate duplicate observations.
        after = datetime.fromisoformat(run['started_at_utc']) + timedelta(days=1)
        day = after.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York')).date().isoformat()
        with model_store_connection(self.path) as conn:
            conn.execute('INSERT INTO research_feature_snapshots VALUES (?,?,?,?,?,?,?,?,?)',
                (PROTOCOL['id'], 'US', 'AAPL', day, after.isoformat(), 100.,
                 json.dumps({'x': 0.3, 'return_20d_pct': 1}), json.dumps({'context_news_sentiment_score': 0.5}), None))
        with patch('app.services.prospective_model_research.train_baseline_model') as train:
            self.service.predict_round(run, after)
            self.service.predict_round(run, after)
            train.assert_not_called()
        with model_store_connection(self.path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM model_decision_feedback WHERE model_name='ridge_regression'").fetchone()[0]
            self.assertEqual(count, 2)

    def test_locked_results_only_release_when_matched_window_completes(self):
        run = {'market': 'HK', 'versions_json': json.dumps({'price_only': 'p', 'price_context': 'c'}), 'round_number': 1}
        for index, date in enumerate(pd.bdate_range('2025-01-01', periods=PROTOCOL['locked_test_dates'])):
            for ticker in PROTOCOL['cohort']['HK']:
                row = {'market': 'HK', 'ticker': ticker, 'decision_date': date.date().isoformat(),
                       'available_at_utc': date.date().isoformat()+'T10:00:00+00:00', 'price': 100.}
                for version in ('p', 'c'):
                    with patch('app.services.model_feedback_service._utc_now_iso', return_value=row['available_at_utc']):
                        self.service._record_prediction(row, version, 'ridge_regression', 2 if version == 'c' else -2)
        self.assertFalse(self.service.release_if_complete(run))
        with model_store_connection(self.path) as conn:
            for date in pd.bdate_range('2025-01-01', periods=PROTOCOL['locked_test_dates']):
                conn.execute("UPDATE model_decision_feedback SET status='evaluated',actual_return_pct=3,benchmark_return_pct=1,outcome_date=? WHERE decision_date=?",
                    ((date + pd.offsets.BDay(5)).date().isoformat(), date.date().isoformat()))
            conn.execute('INSERT INTO research_model_rounds VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                         (PROTOCOL['id'], 'HK', 1, 'locked', self.now.isoformat(), self.now.isoformat(), 'test', '{}', '[]', run['versions_json'], None))
        self.assertTrue(self.service.release_if_complete(run))
        result = self.service.status()['rounds'][0]['result_json']
        self.assertEqual(result['matched_rows'], PROTOCOL['locked_test_dates'] * 4)
        self.assertFalse(result['automatic_promotion'])
        self.assertAlmostEqual(result['metrics']['price_context']['net_return_per_opportunity_pct'], 2.25)
        self.assertEqual(result['paired_evidence']['nonoverlapping_blocks'], 20)
        self.assertTrue(result['paired_evidence']['passed'])


if __name__ == '__main__':
    unittest.main()
