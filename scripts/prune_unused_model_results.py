"""Delete detailed CSV results only for old, rejected, already-pruned fits.

Requires the updated prune_unused_model_binaries.py beside this file. Preview is
read-only. Metrics JSON, database history and all operational models are retained.
Removing detailed results prevents replaying that rejected fit's past backtest.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.prune_unused_model_binaries import plan_cleanup


RESULT_FILES = ('predictions.csv', 'evaluation_table.csv',
                'predictions.csv.gz', 'evaluation_table.csv.gz')


def result_plan(eligibility):
    candidates = []
    skipped = 0
    for name in eligibility['eligible_directories']:
        directory = Path(name)
        binary = directory / 'model.pkl'
        metrics = directory / 'metrics_summary.json'
        # Require surviving summary evidence and an already removed binary.
        if binary.exists() or binary.is_symlink() or metrics.is_symlink() or not metrics.is_file():
            skipped += 1
            continue
        try:
            summary = json.loads(metrics.read_text(encoding='utf-8'))
            if not isinstance(summary, dict):
                raise ValueError('Invalid summary')
        except (OSError, ValueError):
            skipped += 1
            continue
        for filename in RESULT_FILES:
            path = directory / filename
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            if stat.st_nlink != 1:
                continue
            candidates.append({'path': str(path), 'bytes': stat.st_size, 'inode': stat.st_ino,
                               'device': stat.st_dev, 'mtime_ns': stat.st_mtime_ns})
    size = sum(item['bytes'] for item in candidates)
    return {'candidate_files': len(candidates), 'candidate_bytes': size,
            'candidate_gib': round(size / 1024**3, 3), 'skipped_directories': skipped,
            'candidates': candidates}


def remove_results(plan):
    result = {'removed_files': 0, 'reclaimed_bytes': 0}
    try:
        for item in plan['candidates']:
            path = Path(item['path'])
            binary = path.parent / 'model.pkl'
            stat = path.lstat()
            if (binary.exists() or binary.is_symlink() or path.is_symlink()
                    or not path.is_file() or stat.st_nlink != 1
                    or (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) !=
                    (item['device'], item['inode'], item['bytes'], item['mtime_ns'])):
                raise ValueError('Candidate changed; stop all model writers and retry')
            path.unlink()
            result['removed_files'] += 1
            result['reclaimed_bytes'] += item['bytes']
    finally:
        result['reclaimed_gib'] = round(result['reclaimed_bytes'] / 1024**3, 3)
        print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--models-root', default='data/models')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--writers-stopped', action='store_true')
    args = parser.parse_args()
    if args.apply and not args.writers_stopped:
        parser.error('--apply requires --writers-stopped; stop API and all model-writing jobs/timers')
    eligibility = plan_cleanup(args.db, args.models_root)
    plan = result_plan(eligibility)
    if args.apply:
        # Fresh references are read immediately before mutation; writers must
        # remain stopped through the entire operation.
        plan = result_plan(plan_cleanup(args.db, args.models_root))
        remove_results(plan)
    else:
        print(json.dumps({k: v for k, v in plan.items() if k != 'candidates'}, indent=2))


if __name__ == '__main__':
    main()
