"""Filesystem path normalization and containment helpers."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from pathlib import Path


def atomic_write_text(path: str | Path, content: str) -> None:
    """Durably replace *path* with UTF-8 *content* on the same filesystem."""
    target = Path(path)
    fd, tmp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, target)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


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
