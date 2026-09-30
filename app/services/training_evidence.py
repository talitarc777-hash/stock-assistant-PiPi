"""Durable, evidence-gated training; timestamps alone never justify another fit."""
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path

import pandas as pd

from app.core.sqlite_store import model_store_connection


def frame_fingerprint(frame: pd.DataFrame, contract: dict) -> str:
    """Hash the actual ordered feature/label contract, not a download timestamp."""
    digest = hashlib.sha256(json.dumps(contract, sort_keys=True, default=str).encode())
    digest.update(json.dumps([(str(c), str(frame[c].dtype)) for c in frame], sort_keys=True).encode())
    digest.update(pd.util.hash_pandas_object(frame.reset_index(drop=True), index=False).values.tobytes())
    return digest.hexdigest()


class TrainingEvidenceStore:
    def __init__(self, path):
        self.path = Path(path)
        with model_store_connection(self.path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS model_training_evidence (
                job_key TEXT PRIMARY KEY, completed_hash TEXT, completed_dates_json TEXT,
                artifact_path TEXT, claim_hash TEXT, claim_until_utc TEXT, last_reason TEXT)""")

    def claim(self, key, fingerprint, dates, *, now=None, minimum_new_dates=5):
        now = now or datetime.now(UTC)
        dates = sorted(set(map(str, dates)))
        with model_store_connection(self.path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM model_training_evidence WHERE job_key=?', (key,)).fetchone()
            reason = None
            if row:
                if row['claim_until_utc'] and row['claim_until_utc'] > now.isoformat():
                    reason = 'training_lease_active'
                elif row['completed_hash'] and row['artifact_path'] and Path(row['artifact_path']).is_file():
                    previous = json.loads(row['completed_dates_json'] or '[]')
                    added = [date for date in dates if not previous or date > max(previous)]
                    if row['completed_hash'] == fingerprint:
                        reason = 'identical_labeled_dataset'
                    elif len(added) < minimum_new_dates:
                        # Changed old history alone is not permission to repeatedly
                        # retrain on provider revisions. A new contract has a new key.
                        reason = 'insufficient_new_matured_dates'
            if reason:
                conn.execute('UPDATE model_training_evidence SET last_reason=? WHERE job_key=?', (reason, key))
                return False, reason
            conn.execute("""INSERT INTO model_training_evidence(job_key,claim_hash,claim_until_utc,last_reason)
                VALUES (?,?,?,'new_evidence') ON CONFLICT(job_key) DO UPDATE SET
                claim_hash=excluded.claim_hash,claim_until_utc=excluded.claim_until_utc,last_reason=excluded.last_reason""",
                (key, fingerprint, (now + timedelta(hours=6)).isoformat()))
        return True, 'new_evidence'

    def complete(self, key, fingerprint, dates, artifact_path):
        with model_store_connection(self.path) as conn:
            conn.execute("""UPDATE model_training_evidence SET completed_hash=?,completed_dates_json=?,
                artifact_path=?,claim_hash=NULL,claim_until_utc=NULL,last_reason='trained'
                WHERE job_key=? AND claim_hash=?""",
                (fingerprint, json.dumps(sorted(set(map(str, dates)))), str(artifact_path), key, fingerprint))

    def fail(self, key, fingerprint):
        with model_store_connection(self.path) as conn:
            # Preserve the last successful input; failed work can retry after an hour.
            conn.execute("""UPDATE model_training_evidence SET claim_until_utc=?,last_reason='failed'
                WHERE job_key=? AND claim_hash=?""",
                ((datetime.now(UTC) + timedelta(hours=1)).isoformat(), key, fingerprint))

    def unregistered_artifact(self, key):
        """Recover a fit completed before a crash in lifecycle registration."""
        with model_store_connection(self.path) as conn:
            row = conn.execute('SELECT artifact_path FROM model_training_evidence WHERE job_key=?', (key,)).fetchone()
            if not row or not row['artifact_path']:
                return None
            path = Path(row['artifact_path'])
            try:
                metrics = json.loads((path.parent / 'metrics_summary.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                return None
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            registered = (conn.execute('SELECT 1 FROM model_versions WHERE model_version=?',
                                      (metrics.get('model_version'),)).fetchone() if 'model_versions' in tables else None)
            return path if not registered else None
