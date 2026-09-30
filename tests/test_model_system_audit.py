"""Read-only audit and delayed-calibration regression tests."""
import hashlib
from pathlib import Path
import sqlite3
from uuid import uuid4
import unittest

import numpy as np
import pandas as pd

from scripts.audit_model_system import audit_database
from scripts.audit_forward_calibration import delayed_correction


class ModelSystemAuditTests(unittest.TestCase):
    def test_database_audit_is_read_only_and_missing_tables_are_explicit(self):
        path = Path("data") / ("test_audit_" + uuid4().hex + ".db")
        try:
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE unrelated (value TEXT)")
                conn.execute("INSERT INTO unrelated VALUES ('preserve')")
            conn.close()
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            report = audit_database(path)
            self.assertEqual(report["table_counts"], {})
            self.assertIsNone(report["markets"]["HK"]["fallback_pct"])
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
        finally:
            path.unlink(missing_ok=True)

    def test_correction_cannot_see_unmatured_labels(self):
        frame = pd.DataFrame({"actual_future_result": np.arange(100) / 100,
                              "predicted_value": np.full(100, 0.1)})
        before = delayed_correction(frame)
        frame.loc[45:, "actual_future_result"] = 10000
        after = delayed_correction(frame)
        pd.testing.assert_series_equal(before.iloc[:51], after.iloc[:51])
        self.assertLessEqual(float((after - frame.predicted_value).abs().max()), 1.0)
        self.assertEqual(before.iloc[0], 0.1)


if __name__ == "__main__":
    unittest.main()
