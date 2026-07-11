"""Stable application-data paths."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path
    database: Path
    thumbnails: Path
    manifests: Path
    undo_logs: Path
    log: Path

    @classmethod
    def default(cls) -> "AppPaths":
        if sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        else:
            base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        return cls.from_root(base / "PhotoCurator")

    @classmethod
    def from_root(cls, root: str | Path) -> "AppPaths":
        root = Path(root)
        paths = cls(root, root / "catalog.sqlite3", root / "thumbnails", root / "manifests", root / "undo", root / "photo-curator.log")
        for directory in (paths.root, paths.thumbnails, paths.manifests, paths.undo_logs):
            directory.mkdir(parents=True, exist_ok=True)
        return paths

