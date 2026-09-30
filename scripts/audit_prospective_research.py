"""Read-only coverage/experiment audit; never initializes or migrates the database."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.context_observation_store import summarize_context_coverage


def audit(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = [dict(r) for r in conn.execute('SELECT * FROM external_context_observations')] if 'external_context_observations' in tables else []
        rounds = [dict(r) for r in conn.execute('SELECT market,round_number,status,dataset_hash FROM research_model_rounds')] if 'research_model_rounds' in tables else []
        jobs = [dict(r) for r in conn.execute('SELECT last_reason,COUNT(*) AS count FROM model_training_evidence GROUP BY last_reason')] if 'model_training_evidence' in tables else []
        return {'database': str(Path(path).resolve()), 'scope': 'this database only; not proof of private production state',
                'coverage': summarize_context_coverage(rows), 'rounds': rounds, 'training_admission': jobs}
    finally:
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    print(json.dumps(audit(parser.parse_args().db), indent=2))
