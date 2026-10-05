"""Losslessly compress CSV evidence for unused, rejected, pruned model versions.

Preview by default. Uses the same database reference/age protections as binary
cleanup, and skips versions that still have a fitted model. Stop all writers for
apply. Installs and verifies each gzip before removing its original CSV.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.prune_unused_model_binaries import plan_cleanup


def fingerprint(path, opener=open):
    digest = hashlib.sha256()
    with opener(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.digest()


def csv_candidates(plan):
    result = []
    for name in plan['eligible_directories']:
        directory = Path(name)
        if (directory / 'model.pkl').exists():
            continue
        for path in sorted(directory.glob('*.csv')):
            if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
                continue
            result.append(path)
    return result


def compress_file(path):
    path = Path(path)
    before = path.stat()
    if path.is_symlink() or before.st_nlink != 1:
        raise ValueError('Refusing linked file')
    original_hash = fingerprint(path)
    archive = path.with_name(path.name + '.gz')
    temporary = None
    try:
        if archive.exists() or archive.is_symlink():
            if archive.is_symlink() or fingerprint(archive, gzip.open) != original_hash:
                raise ValueError('Existing gzip differs from original; retaining both')
        else:
            # Only one result is staged at a time, not the full model archive.
            # Require worst-case uncompressed size plus a filesystem reserve.
            if shutil.disk_usage(path.parent).free < before.st_size * 1.02 + 64 * 1024**2:
                raise ValueError('Insufficient temporary space for this CSV; original retained')
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.csv-compress-', delete=False) as out:
                temporary = Path(out.name)
                with gzip.GzipFile(filename='', fileobj=out, mode='wb', compresslevel=6, mtime=0) as zipped:
                    with path.open('rb') as source:
                        shutil.copyfileobj(source, zipped, length=1024 * 1024)
                out.flush()
                os.fsync(out.fileno())
            if fingerprint(temporary, gzip.open) != original_hash:
                raise ValueError('Gzip verification failed; original retained')
            if temporary.stat().st_size >= before.st_size:
                return 0
            os.chmod(temporary, before.st_mode & 0o777)
            os.utime(temporary, ns=(before.st_atime_ns, before.st_mtime_ns))
            os.replace(temporary, archive)
            temporary = None
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('CSV changed during compression; original retained')
        if os.name == 'posix':
            fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        saved = before.st_size - archive.stat().st_size
        if saved <= 0:
            return 0
        path.unlink()
        return saved
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--models-root', default='data/models')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--writers-stopped', action='store_true')
    args = parser.parse_args()
    if args.apply and not args.writers_stopped:
        parser.error('--apply requires --writers-stopped')
    paths = csv_candidates(plan_cleanup(args.db, args.models_root))
    result = {'mode': 'apply' if args.apply else 'preview', 'candidate_files': len(paths),
              'source_bytes': sum(p.stat().st_size for p in paths), 'compressed_files': 0, 'reclaimed_bytes': 0}
    try:
        if args.apply:
            for path in paths:
                saved = compress_file(path)
                if saved:
                    result['compressed_files'] += 1
                    result['reclaimed_bytes'] += saved
    finally:
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
