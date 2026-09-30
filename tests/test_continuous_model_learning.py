"""Prospective-data and matched-forward-evidence correctness, without provider calls."""
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import uuid

import pandas as pd

from app.services.context_observation_store import ContextObservationStore
from app.services.model_feedback_service import ModelFeedbackService
from app.services.model_lifecycle_service import ModelLifecycleService
from app.services.paired_model_evidence import compare_paired_rows
from tests import test_model_feedback_service as feedback_fixtures


class ContextObservationTests(unittest.TestCase):
    def setUp(self):
        self.path = Path('data') / f'test_context_{uuid.uuid4().hex}.db'
        self.store = ContextObservationStore(str(self.path))
        self.now = datetime(2026, 9, 12, 10, tzinfo=UTC)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_point_in_time_availability_late_revision_and_idempotence(self):
        original = {'fetched_at_utc': self.now.isoformat(), 'news_sentiment_score': 0.2}
        first = self.store.record('700.hk', 'HK', original, now=self.now)
        self.assertIsNone(self.store.as_of('0700', 'HK', self.now - timedelta(seconds=1)))
        self.assertEqual(self.store.record('0700', 'HK', original, now=self.now + timedelta(hours=1)), first)
        self.assertEqual(self.store.as_of('0700', 'HK', self.now)['available_at_utc'], self.now.isoformat())
        revised = {**original, 'news_sentiment_score': -0.8}
        self.store.record('0700', 'HK', revised, now=self.now + timedelta(hours=2))
        self.assertEqual(self.store.as_of('0700', 'HK', self.now)['features']['news_sentiment_score'], 0.2)
        self.assertEqual(self.store.as_of('0700', 'HK', self.now + timedelta(hours=3))['features']['news_sentiment_score'], -0.8)
        self.assertIsNone(self.store.as_of('0700', 'HK', self.now + timedelta(days=1)))

    def test_unknown_values_are_missing_not_zero_and_raw_text_is_not_stored(self):
        self.store.record('aapl', 'US', {'news_sentiment_score': float('nan'), 'article_body': 'SECRET',
                                      'missing_sources': ['analyst']}, now=self.now)
        row = self.store.as_of('AAPL', 'US', self.now)
        self.assertIsNone(row['features']['news_sentiment_score'])
        self.assertNotIn('SECRET', json.dumps(row))
        self.assertFalse(self.store.status()['used_for_model_training'])

    def test_unavailable_feed_default_counts_do_not_become_zero_attention(self):
        self.store.record('AAPL', 'US', {'social_mention_count': 0, 'official_event_count': 0,
                                       'missing_sources': ['reddit_social_search', 'sec_edgar_filings']}, now=self.now)
        features = self.store.as_of('AAPL', 'US', self.now)['features']
        self.assertIsNone(features['social_mention_count'])
        self.assertIsNone(features['official_event_count'])

    def test_naive_or_future_timestamps_rejected(self):
        with self.assertRaises(ValueError):
            self.store.record('AAPL', 'US', {'fetched_at_utc': '2026-09-12T10:00:00'}, now=self.now)
        with self.assertRaises(ValueError):
            self.store.record('AAPL', 'US', {'fetched_at_utc': (self.now + timedelta(seconds=1)).isoformat()}, now=self.now)

    def test_bounded_cursor_survives_restart_and_separates_markets(self):
        universe = ['0700', '1810', '0005', '9988']
        self.assertEqual(self.store.claim_batch('HK', universe, self.now), ['0005', '0700'])
        restarted = ContextObservationStore(str(self.path))
        self.assertEqual(restarted.claim_batch('HK', universe, self.now), [])
        self.assertEqual(restarted.claim_batch('US', ['MSFT', 'AAPL'], self.now), ['AAPL', 'MSFT'])
        self.assertEqual(restarted.claim_batch('HK', universe, self.now + timedelta(hours=1)), ['1810', '9988'])

    def test_independent_collector_does_not_require_a_buy_signal(self):
        from app.services.context_collection_service import collect_due_context
        profiles = MagicMock()
        profiles.list_effective_watchlist_tickers.side_effect = lambda market: ['0700'] if market == 'HK' else ['AAPL']
        with patch('app.services.context_collection_service.get_settings', return_value=SimpleNamespace(
            context_archive_enabled=True, external_context_enabled=True)), patch(
            'app.services.context_collection_service.get_context_observation_store', return_value=self.store
        ), patch('app.services.context_collection_service.get_user_profile_store', return_value=profiles), patch(
            'app.services.context_collection_service.get_active_universe', return_value=['MSFT']
        ), patch('app.services.context_collection_service.build_external_market_context', return_value={'observation_id': 1}) as provider:
            result = collect_due_context(self.now)
            self.assertEqual(result['attempted'], 3)
            self.assertEqual(result['recorded'], 3)
            self.assertIn('0700.HK', [c.args[0] for c in provider.call_args_list])
            self.assertEqual(collect_due_context(self.now)['attempted'], 0)


def paired_rows(count=24):
    candidate, incumbent = [], []
    for index in range(count):
        day = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=index * 8)
        base = dict(ticker='AAPL', decision_date=day.date().isoformat(),
                    outcome_date=(day + timedelta(days=7)).date().isoformat(),
                    recorded_at_utc=day.isoformat(), benchmark='VOO', horizon_days=5,
                    status='evaluated', task_type='regression', decision_price=100,
                    actual_return_pct=3 if index % 2 == 0 else -3, context_json='{}')
        candidate.append({**base, 'prediction_value': 2 if index % 2 == 0 else -2})
        incumbent.append({**base, 'prediction_value': -2 if index % 2 == 0 else 2})
    return candidate, incumbent


class PairedEvidenceTests(unittest.TestCase):
    def test_strong_matched_improvement_passes(self):
        result = compare_paired_rows(*paired_rows())
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['nonoverlapping_blocks'], 24)
        self.assertFalse(result['is_portfolio_profit'])

    def test_same_model_and_disjoint_dates_cannot_promote(self):
        candidate, incumbent = paired_rows()
        self.assertFalse(compare_paired_rows(candidate, candidate)['passed'])
        result = compare_paired_rows(candidate[:12], incumbent[12:])
        self.assertEqual(result['matched_rows'], 0)
        self.assertFalse(result['passed'])

    def test_many_same_day_tickers_are_not_independent_evidence(self):
        candidate, incumbent = paired_rows(1)
        candidate = [{**candidate[0], 'ticker': f'T{i}'} for i in range(100)]
        incumbent = [{**incumbent[0], 'ticker': f'T{i}'} for i in range(100)]
        result = compare_paired_rows(candidate, incumbent)
        self.assertEqual(result['matched_rows'], 100)
        self.assertEqual(result['nonoverlapping_blocks'], 1)
        self.assertFalse(result['passed'])

    def test_mismatched_prices_and_late_predictions_are_excluded(self):
        candidate, incumbent = paired_rows()
        candidate[0]['decision_price'] = 101
        candidate[1]['recorded_at_utc'] = candidate[1]['outcome_date'] + 'T00:00:00+00:00'
        result = compare_paired_rows(candidate, incumbent)
        self.assertEqual(result['matched_rows'], 22)
        self.assertEqual(result['discarded_mismatches'], 1)

    def test_zero_shadow_quantity_does_not_remove_comparison_costs(self):
        candidate, incumbent = paired_rows()
        for row in candidate + incumbent:
            row['quantity'] = 0
            row['strategy_net_return_pct'] = 10000
            row['context_json'] = json.dumps({'comparison_cost_pct': 10})
        result = compare_paired_rows(candidate, incumbent)
        self.assertFalse(result['passed'])
        self.assertEqual(result['net_delta_pct_interval_95']['mean'], 0)


class ForwardWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.path = Path('data') / f'test_forward_workflow_{uuid.uuid4().hex}.db'
        self.service = ModelFeedbackService(str(self.path))

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_missing_history_does_not_starve_other_feedback(self):
        payload = feedback_fixtures.ModelFeedbackServiceTests._payload
        self.service.record_decision(payload(date='2026-01-02', model_version='missing'))
        self.service.record_decision(payload(date='2026-01-05', model_version='available'))
        with patch.object(self.service, 'evaluate_pending_horizons', return_value={'evaluated': 0, 'pending': 0, 'errors': []}), patch.object(
            self.service, 'evaluate_pending_benchmark_shadows', return_value={'evaluated': 0, 'pending': 0, 'errors': []}
        ):
            self.service.evaluate_pending(limit=1, price_loader=lambda *_: pd.DataFrame())
        self.assertEqual(self.service._pending_feedback_for_evaluation(limit=1)[0]['model_version'], 'available')
        restarted = ModelFeedbackService(str(self.path))
        self.assertEqual(len(restarted.list_feedback()), 2)

    def test_live_drift_requires_matured_version_specific_dates(self):
        self.assertEqual(self.service.get_live_monitor(model_version='v1', market='US')['status'], 'waiting_for_evidence')
        for index, day in enumerate(pd.bdate_range('2025-01-01', periods=60)):
            payload = feedback_fixtures.ModelFeedbackServiceTests._payload(date=day.date().isoformat())
            with patch('app.services.model_feedback_service._utc_now_iso', return_value=day.isoformat() + '+00:00'):
                self.service.record_decision(payload)
            with self.service._connect() as conn:
                conn.execute("""UPDATE model_decision_feedback SET status='evaluated',outcome_date=?,
                    actual_return_pct=?,direction_correct=? WHERE decision_date=?""",
                    ((day + pd.Timedelta(days=7)).date().isoformat(), 2 if index < 30 else -3,
                     int(index < 30), day.date().isoformat()))
        result = self.service.get_live_monitor(model_version='v1', market='US')
        self.assertTrue(result['drift_detected'])
        self.assertEqual(result['recent']['dates'], 30)
        self.assertFalse(self.service.get_live_monitor(model_version='v1', market='HK')['drift_detected'])
        self.assertFalse(self.service.get_live_monitor(model_version='other', market='US')['drift_detected'])

    def test_consumed_outcome_alarm_does_not_retrain_on_same_evidence(self):
        lifecycle = ModelLifecycleService(str(self.path))
        active = dict(ticker='AAPL', period='2y', target_name='target_5d_return', model_name='ridge', model_version='v1')
        with patch.object(lifecycle.version_service, 'list_versions', return_value=[active]), patch.object(
            lifecycle.version_service, 'get_active', return_value=active
        ), patch.object(lifecycle.feedback_service, 'get_live_monitor', return_value={
            'drift_detected': True, 'status': 'deteriorating', 'latest_outcome_date': '2026-09-10'
        }):
            alarms = lifecycle.detect_retrain_triggers(market='US')
            self.assertEqual(len(alarms), 1)
            lifecycle.set_state('last_consumed_triggers_json', json.dumps(alarms))
            self.assertEqual(lifecycle.detect_retrain_triggers(market='US'), [])

    def test_incumbent_and_challenger_inference_use_same_input(self):
        from app.services.live_virtual_trader import _build_challenger_shadow_prediction
        lifecycle = MagicMock()
        lifecycle.resolve_shadow_model_candidates.return_value = [dict(ticker='0700', period='2y', model_version='new')]
        lifecycle.version_service.get_active.return_value = dict(ticker='0700', period='2y', model_version='old')
        row = pd.Series({'date': '2026-09-10', 'close': 100})
        with patch('app.services.live_virtual_trader._predict_shadow_candidate', side_effect=[
            {'status': 'available', 'execution_enabled': False}, {'status': 'available', 'execution_enabled': False}
        ]) as infer:
            result = _build_challenger_shadow_prediction(ticker='0700', market='HK', target_name='target_5d_return',
                periods=('2y',), latest_row=row, stationary_latest_row=None, lifecycle_service=lifecycle)
        self.assertIs(infer.call_args_list[0].args[4], infer.call_args_list[1].args[4])
        self.assertFalse(result['execution_enabled'])
        self.assertFalse(result['paired_incumbent']['execution_enabled'])

    def test_persisted_pair_evidence_and_incumbent_source_are_connected(self):
        candidate, incumbent = paired_rows()
        for left, right in zip(candidate, incumbent):
            payload = feedback_fixtures.ModelFeedbackServiceTests._payload(date=left['decision_date'])
            for row, version, role in ((left, 'new', 'challenger'), (right, 'old', 'incumbent')):
                shadow = dict(status='available', model_name='ridge_regression', model_period='2y',
                              model_ticker='AAPL', model_version=version, model_role=role,
                              prediction_value=row['prediction_value'], task_type='regression')
                with patch('app.services.model_feedback_service._utc_now_iso', return_value=row['recorded_at_utc']):
                    self.assertTrue(self.service.record_challenger_shadow(payload, shadow))
                with self.service._connect() as conn:
                    conn.execute("""UPDATE model_decision_feedback SET status='evaluated',outcome_date=?,
                        actual_return_pct=? WHERE model_version=? AND decision_date=?""",
                        (row['outcome_date'], row['actual_return_pct'], version, row['decision_date']))
        evidence = self.service.get_paired_model_evidence(challenger_version='new', incumbent_version='old', market='US')
        self.assertTrue(evidence['passed'], evidence)
        rows = self.service.list_feedback(limit=100)
        self.assertEqual({r['decision_source'] for r in rows}, {'shadow_challenger', 'shadow_incumbent'})
        self.assertTrue(all(r['quantity'] == 0 and r['action'] == 'no_action' for r in rows))
        self.assertEqual(self.service.get_paired_model_evidence(
            challenger_version='new', incumbent_version='old', market='HK')['matched_rows'], 0)
        self.assertEqual(self.service.get_paired_model_evidence(
            challenger_version='new', incumbent_version='old', market='US',
            since='2026-01-01T00:00:00+00:00')['matched_rows'], 0)


if __name__ == '__main__':
    unittest.main()
