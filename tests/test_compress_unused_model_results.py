from contextlib import closing
import gzip
import sqlite3
import unittest

from app.core.artifact_csv import resolve_csv_artifact
from scripts.compress_unused_model_results import compress_file, csv_candidates
from tests import test_prune_unused_model_binaries as fixtures


class CompressUnusedResultsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PruneUnusedBinaryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.binary = self.fixture.seed_old()
        self.path = self.binary.parent / 'predictions.csv'
        self.path.write_bytes(b'date,prediction\n' + b'2026-01-01,1\n' * 10000)

    def test_round_trip_and_reader_resolution(self):
        original = self.path.read_bytes()
        saved = compress_file(self.path)
        archive = resolve_csv_artifact(self.path)
        self.assertGreater(saved, 0)
        self.assertFalse(self.path.exists())
        with gzip.open(archive, 'rb') as stream:
            self.assertEqual(stream.read(), original)
        self.assertTrue(self.binary.exists())
        self.assertTrue((self.path.parent / 'metrics_summary.json').exists())

    def test_application_reads_compressed_historical_evidence(self):
        from app.services.model_results import _read_csv_file
        self.binary.unlink()
        original = _read_csv_file(self.path)
        compress_file(self.path)
        self.assertTrue(original.equals(_read_csv_file(self.path)))

    def test_corrupt_existing_archive_preserves_original(self):
        self.path.with_name(self.path.name + '.gz').write_bytes(b'bad gzip')
        with self.assertRaises(OSError):
            compress_file(self.path)
        self.assertTrue(self.path.exists())

    def test_verified_existing_archive_recovers_interrupted_cleanup(self):
        original = self.path.read_bytes()
        with gzip.open(self.path.with_name(self.path.name + '.gz'), 'wb') as stream:
            stream.write(original)
        self.assertGreater(compress_file(self.path), 0)
        self.assertFalse(self.path.exists())

    def test_only_pruned_unused_versions_are_candidates(self):
        self.assertEqual(csv_candidates(self.fixture.plan()), [])
        self.binary.unlink()
        self.assertEqual(csv_candidates(self.fixture.plan()), [self.path])
        with closing(sqlite3.connect(self.fixture.db, isolation_level=None)) as conn:
            conn.execute('INSERT INTO active_model_deployments VALUES (?,?)', ('active', 'old'))
        self.assertEqual(csv_candidates(self.fixture.plan()), [])

    def test_small_incompressible_file_not_removed(self):
        self.path.write_bytes(b'x')
        self.assertEqual(compress_file(self.path), 0)
        self.assertTrue(self.path.exists())
        self.assertFalse(self.path.with_name(self.path.name + '.gz').exists())


if __name__ == '__main__':
    unittest.main()
