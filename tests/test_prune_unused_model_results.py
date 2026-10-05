from contextlib import redirect_stdout, closing
import io
import sqlite3
import unittest

from scripts.prune_unused_model_results import result_plan, remove_results
from tests import test_prune_unused_model_binaries as fixtures


class PruneUnusedResultsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PruneUnusedBinaryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.binary = self.fixture.seed_old()
        self.csv = self.binary.parent / 'predictions.csv'

    def plan(self):
        return result_plan(self.fixture.plan())

    def test_preview_keeps_results_and_fit_with_binary_is_excluded(self):
        self.assertEqual(self.plan()['candidate_files'], 0)
        self.binary.unlink()
        self.assertEqual(self.plan()['candidate_files'], 1)
        self.assertTrue(self.csv.is_file())

    def test_apply_keeps_summary_database_and_unrelated_files(self):
        self.binary.unlink()
        unrelated = self.csv.parent / 'custom_evidence.csv'
        unrelated.write_text('custom retained evidence')
        (self.csv.parent / 'evaluation_table.csv.gz').write_bytes(b'archive')
        db_bytes = self.fixture.db.read_bytes()
        with redirect_stdout(io.StringIO()):
            result = remove_results(self.plan())
        self.assertEqual(result['removed_files'], 2)
        self.assertFalse(self.csv.exists())
        self.assertTrue((self.csv.parent / 'metrics_summary.json').exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual(self.fixture.db.read_bytes(), db_bytes)
        self.assertEqual(self.plan()['candidate_files'], 0)

    def test_missing_or_invalid_summary_excludes_deletion(self):
        self.binary.unlink()
        metrics = self.csv.parent / 'metrics_summary.json'
        metrics.write_text('invalid json')
        self.assertEqual(self.plan()['candidate_files'], 0)
        metrics.unlink()
        self.assertEqual(self.plan()['candidate_files'], 0)

    def test_pending_feedback_preserves_detailed_results(self):
        self.binary.unlink()
        with closing(sqlite3.connect(self.fixture.db, isolation_level=None)) as conn:
            conn.execute('INSERT INTO model_decision_feedback VALUES (?,?)', ('old', 'pending'))
        self.assertEqual(self.plan()['candidate_files'], 0)

    def test_reappearing_binary_or_changed_csv_aborts(self):
        self.binary.unlink()
        plan = self.plan()
        self.binary.write_bytes(b'new fit')
        with redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            remove_results(plan)
        self.binary.unlink()
        self.csv.write_text('replaced results')
        with redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            remove_results(plan)
        self.assertTrue(self.csv.exists())


if __name__ == '__main__':
    unittest.main()
