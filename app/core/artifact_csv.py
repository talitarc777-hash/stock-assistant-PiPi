"""Locate CSV evidence retained either as plain CSV or lossless gzip archives."""
from pathlib import Path


def resolve_csv_artifact(path):
    path = Path(path)
    if path.is_file():
        return path
    compressed = path.with_name(path.name + '.gz')
    return compressed if compressed.is_file() else path
