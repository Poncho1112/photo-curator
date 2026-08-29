"""Filesystem path normalization and containment helpers."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path


def normalized_absolute(path: str | Path) -> str:
    """Return a normalized absolute path without resolving symlinks."""
    return os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path))))


def is_beneath(path: str, root: str) -> bool:
    """Return whether two already-normalized paths have a root relationship."""
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def is_contained(path: str | Path, roots: Iterable[str | Path]) -> bool:
    """Check containment under a selected root or that root's real location."""
    normalized_path = normalized_absolute(path)
    for root in roots:
        selected_root = normalized_absolute(root)
        real_root = normalized_absolute(os.path.realpath(os.fspath(root)))
        if is_beneath(normalized_path, selected_root) or is_beneath(normalized_path, real_root):
            return True
    return False
