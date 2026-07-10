"""Exact duplicate detection based on streamed SHA-256 hashes."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Iterable


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_exact_duplicates(paths: Iterable[str | Path]) -> list[list[Path]]:
    by_size: dict[int, list[Path]] = defaultdict(list)
    for item in paths:
        path = Path(item)
        if path.is_file():
            by_size[path.stat().st_size].append(path)

    by_hash: dict[str, list[Path]] = defaultdict(list)
    for candidates in by_size.values():
        if len(candidates) > 1:
            for path in candidates:
                by_hash[sha256_file(path)].append(path)
    return [group for group in by_hash.values() if len(group) > 1]

