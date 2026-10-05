"""Reclaim old rejected fit binaries; retain all CSV/JSON historical evidence.

Default is a read-only preview. Stop all model writers before --apply. No database
rows, canonical artifacts, research artifacts or version directories are deleted.
The database is opened read-only and must contain the version/deployment schema.
"""
import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Version timestamp must have a timezone')
    return parsed.astimezone(timezone.utc)


def version_references(value):
    """Find embedded provenance references without treating every string as an ID."""
    result = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if 'version' in key and isinstance(child, str):
                result.add(child)
            result.update(version_references(child))
    elif isinstance(value, list):
        for child in value:
            result.update(version_references(child))
    return result


def plan_cleanup(db_path, models_root, *, minimum_age_days=7, keep_rejected=2, now=None):
    if minimum_age_days < 1 or keep_rejected < 1:
        raise ValueError('Keep at least one recent rejected fit and one day of age protection')
    root = Path(models_root).resolve(strict=True)
    db = Path(db_path).resolve(strict=True)
    if not root.is_dir() or not db.is_file():
        raise ValueError('Existing model directory and database are required')
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=minimum_age_days)
    protected = set()
    protected_paths = set()
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {'model_versions', 'active_model_deployments', 'model_deployment_events'}
        if not required.issubset(tables):
            raise ValueError('Version/deployment schema missing; refusing to infer unused files')
        rows = [dict(r) for r in conn.execute('SELECT * FROM model_versions')]
        for row in conn.execute('SELECT * FROM active_model_deployments'):
            protected.update(filter(None, (row['active_model_version'], row['previous_model_version'])))
        # Even historical deployment binaries are retained for reproducibility.
        for row in conn.execute('SELECT * FROM model_deployment_events'):
            protected.update(filter(None, (row['from_model_version'], row['to_model_version'])))
        for row in rows:
            if row.get('parent_model_version'):
                protected.add(row['parent_model_version'])
            if row['lifecycle_status'] != 'rejected' or row['model_role'] != 'none' or row['is_validated']:
                protected.add(row['model_version'])
            if json.loads(row['metrics_json'] or '{}').get('experimental_only'):
                protected.add(row['model_version'])
        if 'model_decision_feedback' in tables:
            protected.update(r[0] for r in conn.execute(
                "SELECT DISTINCT model_version FROM model_decision_feedback WHERE status='pending' AND model_version IS NOT NULL"))
        if 'research_model_rounds' in tables:
            for row in conn.execute('SELECT versions_json FROM research_model_rounds'):
                protected.update(json.loads(row[0] or '{}').values())
        if 'model_training_evidence' in tables:
            for row in conn.execute('SELECT artifact_path FROM model_training_evidence WHERE artifact_path IS NOT NULL'):
                p = Path(row[0])
                protected_paths.add((p if p.is_absolute() else Path.cwd() / p).resolve())
        # Registry layouts changed over time: inspect existing JSON provenance
        # and path columns without requiring a migration or writing any rows.
        for table in ('model_registry', 'market_model_registry'):
            if table not in tables:
                continue
            for row in conn.execute('SELECT * FROM ' + table):
                for key in row.keys():
                    value = row[key]
                    if value and 'json' in key:
                        protected.update(version_references(json.loads(value)))
                    elif value and ('path' in key or key == 'artifact_dir'):
                        p = Path(value)
                        protected_paths.add((p if p.is_absolute() else Path.cwd() / p).resolve())
    families = defaultdict(list)
    for row in rows:
        if row['lifecycle_status'] == 'rejected':
            family = tuple(row[k] for k in ('market', 'ticker', 'period', 'target_name', 'model_name'))
            families[family].append(row)
    for group in families.values():
        group.sort(key=lambda r: timestamp(r['created_at_utc']), reverse=True)
        protected.update(r['model_version'] for r in group[:keep_rejected])
    candidates = []
    for row in rows:
        version = row['model_version']
        if version in protected or timestamp(row['created_at_utc']) >= cutoff:
            continue
        directory = Path(row['artifact_dir'])
        directory = directory if directory.is_absolute() else Path.cwd() / directory
        binary = directory / 'model.pkl'
        # Refuse links or alternate layouts, including an alias to a canonical fit.
        if not binary.exists() or binary.is_symlink() or directory.is_symlink():
            continue
        resolved = binary.resolve(strict=True)
        if (not resolved.is_relative_to(root) or resolved != binary.absolute()
                or directory.parent.name != 'versions' or directory.name != version):
            continue
        if resolved in protected_paths or resolved.parent in protected_paths:
            continue
        stat = binary.stat()
        if not binary.is_file() or stat.st_nlink != 1:
            continue
        candidates.append({'model_version': version, 'path': str(resolved),
                           'bytes': stat.st_size, 'inode': stat.st_ino,
                           'device': stat.st_dev, 'mtime_ns': stat.st_mtime_ns})
    return {'candidate_files': len(candidates), 'candidate_bytes': sum(c['bytes'] for c in candidates),
            'protected_versions': len(protected), 'candidates': candidates}


def apply_cleanup(plan):
    removed = 0
    reclaimed = 0
    for item in plan['candidates']:
        path = Path(item['path'])
        stat = path.lstat()
        if (path.is_symlink() or not path.is_file() or stat.st_nlink != 1
                or (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) !=
                (item['device'], item['inode'], item['bytes'], item['mtime_ns'])):
            raise ValueError('Candidate changed during cleanup; stop model writers and retry')
        path.unlink()
        removed += 1
        reclaimed += item['bytes']
    return {'removed_files': removed, 'reclaimed_bytes': reclaimed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, help='Actual production profile/model SQLite database')
    parser.add_argument('--models-root', default='data/models')
    parser.add_argument('--minimum-age-days', type=int, default=7)
    parser.add_argument('--keep-rejected', type=int, default=2)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--writers-stopped', action='store_true', help='Confirm API, bot and training timers are stopped')
    args = parser.parse_args()
    if args.apply and not args.writers_stopped:
        parser.error('--apply requires --writers-stopped; also stop automatic deployment/training jobs')
    plan = plan_cleanup(args.db, args.models_root, minimum_age_days=args.minimum_age_days,
                        keep_rejected=args.keep_rejected)
    if args.apply:
        # Recheck all DB references immediately before removing files.
        plan = plan_cleanup(args.db, args.models_root, minimum_age_days=args.minimum_age_days,
                            keep_rejected=args.keep_rejected)
        result = apply_cleanup(plan)
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(plan, indent=2))


if __name__ == '__main__':
    main()
