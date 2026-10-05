"""Exercise deletion boundaries against actual files and a read-only SQLite DB."""
from datetime import datetime, timezone
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.prune_unused_model_binaries import plan_cleanup, apply_cleanup


class PruneUnusedBinaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'models'
        self.root.mkdir()
        self.db = Path(self.temp.name) / 'state.db'
        self.now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        with closing(sqlite3.connect(self.db, isolation_level=None)) as conn:
            conn.executescript('''
                CREATE TABLE model_versions(model_version TEXT, market TEXT, ticker TEXT,
                period TEXT, target_name TEXT, model_name TEXT, lifecycle_status TEXT,
                model_role TEXT, is_validated INTEGER, metrics_json TEXT, parent_model_version TEXT,
                artifact_dir TEXT, created_at_utc TEXT);
                CREATE TABLE active_model_deployments(active_model_version TEXT,previous_model_version TEXT);
                CREATE TABLE model_deployment_events(from_model_version TEXT,to_model_version TEXT);
                CREATE TABLE model_decision_feedback(model_version TEXT,status TEXT);
                CREATE TABLE research_model_rounds(versions_json TEXT);
                CREATE TABLE model_training_evidence(artifact_path TEXT);
                CREATE TABLE market_model_registry(metrics_json TEXT);
            ''')

    def add(self, version, *, status='rejected', date='2026-09-01T00:00:00+00:00', directory=None):
        directory = directory or self.root / 'HK' / '0700' / '2y' / 'target' / 'ridge' / 'versions' / version
        directory.mkdir(parents=True, exist_ok=True)
        binary = directory / 'model.pkl'
        binary.write_bytes(b'old fitted estimator')
        (directory / 'metrics_summary.json').write_text('{}')
        (directory / 'predictions.csv').write_text('date,prediction\n2026-01-01,1\n')
        with closing(sqlite3.connect(self.db, isolation_level=None)) as conn:
            conn.execute('INSERT INTO model_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (version, 'HK', '0700', '2y', 'target', 'ridge', status,
                          'none' if status == 'rejected' else 'challenger', 0, '{}', None,
                          str(directory), date))
        return binary

    def plan(self):
        return plan_cleanup(self.db, self.root, now=self.now, keep_rejected=1)

    def seed_old(self):
        old = self.add('old')
        self.add('latest', date='2026-10-01T00:00:00+00:00')
        return old

    def test_preview_readonly_and_apply_preserves_evidence(self):
        binary = self.seed_old()
        original_db = self.db.read_bytes()
        plan = self.plan()
        self.assertTrue(binary.exists())
        self.assertEqual(plan['candidate_files'], 1)
        result = apply_cleanup(plan)
        self.assertEqual(result['removed_files'], 1)
        self.assertFalse(binary.exists())
        self.assertTrue((binary.parent / 'predictions.csv').exists())
        self.assertTrue((binary.parent / 'metrics_summary.json').exists())
        self.assertEqual(original_db, self.db.read_bytes())

    def test_active_shadow_research_and_recent_rejected_kept(self):
        self.seed_old()
        for status in ('active', 'shadow', 'research_shadow', 'retired', 'eligible'):
            self.add(status, status=status)
        self.add('recent', date='2026-10-04T00:00:00+00:00')
        self.assertEqual([c['model_version'] for c in self.plan()['candidates']], ['old'])

    def test_all_reference_sources_protect_even_rejected_versions(self):
        for source in ('pointer', 'event', 'pending', 'research', 'training', 'registry', 'parent'):
            with self.subTest(source=source):
                binary = self.add(source)
                with closing(sqlite3.connect(self.db, isolation_level=None)) as conn:
                    if source == 'pointer':
                        conn.execute('INSERT INTO active_model_deployments VALUES (?,?)', ('active', source))
                    elif source == 'event':
                        conn.execute('INSERT INTO model_deployment_events VALUES (?,?)', (source, None))
                    elif source == 'pending':
                        conn.execute('INSERT INTO model_decision_feedback VALUES (?,?)', (source, 'pending'))
                    elif source == 'research':
                        conn.execute('INSERT INTO research_model_rounds VALUES (?)', (json.dumps({'price_only': source}),))
                    elif source == 'training':
                        conn.execute('INSERT INTO model_training_evidence VALUES (?)', (str(binary),))
                    elif source == 'registry':
                        conn.execute('INSERT INTO market_model_registry VALUES (?)', (json.dumps({'model_version': source}),))
                    elif source == 'parent':
                        conn.execute("UPDATE model_versions SET parent_model_version=? WHERE model_version='registry'", (source,))
        self.add('latest', date='2026-10-01T00:00:00+00:00')
        self.assertEqual(self.plan()['candidate_files'], 0)

    def test_canonical_and_outside_root_paths_excluded(self):
        self.seed_old()
        self.add('canonical', directory=self.root / 'canonical')
        self.add('outside', directory=Path(self.temp.name) / 'elsewhere' / 'versions' / 'outside')
        self.assertEqual([c['model_version'] for c in self.plan()['candidates']], ['old'])

    def test_missing_schema_refuses_cleanup(self):
        with closing(sqlite3.connect(self.db, isolation_level=None)) as conn:
            conn.execute('DROP TABLE active_model_deployments')
        with self.assertRaises(ValueError):
            self.plan()

    def test_changed_file_refuses_removal(self):
        binary = self.seed_old()
        plan = self.plan()
        binary.write_bytes(b'replaced by newer fit')
        with self.assertRaises(ValueError):
            apply_cleanup(plan)
        self.assertTrue(binary.exists())


if __name__ == '__main__':
    unittest.main()
