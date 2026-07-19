"""Hash-verified moves to trash with an append-only deletion log."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from engine.duplicates.exact_duplicates import sha256_file

TrashFn = Callable[[Path], Path | None]

WINDOWS_TRASH_BACKEND = "windows_ifileoperation"
SEND2TRASH_BACKEND = "send2trash"
INJECTED_TRASH_BACKEND = "injected"


@dataclass(frozen=True, slots=True)
class TrashResult:
    source: Path
    trashed: bool
    trashed_to: Path | None = None
    error: str | None = None


def _send_to_trash_send2trash(path: Path) -> None:
    try:
        from send2trash import send2trash
    except ImportError as exc:
        raise ImportError(
            "Deleting files requires send2trash; install it with 'pip install send2trash'."
        ) from exc
    send2trash(path)


def _send_to_trash_windows(path: Path) -> Path:
    """Windows default: Shell IFileOperation recycle returning the exact $R path."""
    from engine.delete.windows_recycle_bin import send_to_recycle_bin

    return send_to_recycle_bin(path)


def default_trash_fn(*, platform: str | None = None) -> TrashFn:
    """Return the platform default trash backend (lazy; safe to import on non-Windows)."""
    name = platform if platform is not None else os.name
    if name == "nt":
        return _send_to_trash_windows
    return _send_to_trash_send2trash  # type: ignore[return-value]


def default_trash_backend_name(*, platform: str | None = None) -> str:
    name = platform if platform is not None else os.name
    if name == "nt":
        return WINDOWS_TRASH_BACKEND
    return SEND2TRASH_BACKEND


class DeleteService:
    def __init__(
        self,
        deletion_log: str | Path,
        trash_fn: TrashFn | None = None,
        hasher: Callable[[Path], str] | None = None,
        *,
        trash_backend: str | None = None,
        platform: str | None = None,
    ) -> None:
        self.deletion_log = Path(deletion_log)
        if trash_fn is None:
            self.trash_fn: TrashFn = default_trash_fn(platform=platform)
            self.trash_backend = trash_backend or default_trash_backend_name(platform=platform)
        else:
            self.trash_fn = trash_fn
            self.trash_backend = trash_backend or INJECTED_TRASH_BACKEND
        self.hasher = hasher or sha256_file

    def delete_paths(self, targets: Iterable[tuple[str | Path, str]]) -> list[TrashResult]:
        results: list[TrashResult] = []
        for item, expected_sha256 in targets:
            source = Path(item)
            if not source.is_file():
                results.append(TrashResult(source, False, error="source file does not exist"))
                continue
            try:
                actual_sha256 = self.hasher(source)
            except OSError as exc:
                results.append(TrashResult(source, False, error=str(exc)))
                continue
            if actual_sha256 != expected_sha256:
                results.append(
                    TrashResult(source, False, error="file changed since indexing; not deleted")
                )
                continue

            try:
                source_size = source.stat().st_size
            except OSError as exc:
                results.append(TrashResult(source, False, error=str(exc)))
                continue

            original = source.resolve()
            try:
                destination = self.trash_fn(source)
            except OSError as exc:
                results.append(TrashResult(source, False, error=str(exc)))
                continue

            # Windows production backend must return an exact path; injected backends
            # may still return None (legacy). A Windows default that returns None is
            # treated as failure so undo is never logged without a destination.
            if destination is None and self.trash_backend == WINDOWS_TRASH_BACKEND:
                results.append(
                    TrashResult(
                        source,
                        False,
                        error=(
                            "Windows trash backend returned no exact recycled path; "
                            "file may be in Recycle Bin but undo destination is unknown"
                        ),
                    )
                )
                continue

            trashed_to = Path(destination).resolve() if destination is not None else None
            self._write_log(
                original,
                expected_sha256,
                trashed_to,
                source_size=source_size,
            )
            results.append(TrashResult(source, True, trashed_to))
        return results

    def _write_log(
        self,
        source: Path,
        sha256: str,
        trashed_to: Path | None,
        *,
        source_size: int,
    ) -> None:
        self.deletion_log.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps(
            {
                "original_path": str(source),
                "sha256": sha256,
                "trashed_to": str(trashed_to) if trashed_to is not None else None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trashed": True,
                "trash_backend": self.trash_backend,
                "source_size": source_size,
            }
        )
        with self.deletion_log.open("a", encoding="utf-8") as log:
            log.write(entry + "\n")
            log.flush()
            os.fsync(log.fileno())
