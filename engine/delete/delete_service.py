"""Hash-verified moves to trash with an append-only deletion log."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from engine.duplicates.exact_duplicates import sha256_file
from engine.fsutil import is_contained

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
    undo_logged: bool = True


@dataclass(frozen=True, slots=True)
class DeleteGroup:
    survivor: Path
    sha256: str
    targets: tuple[Path, ...]


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
        """Delete verified targets without survivor or containment guarantees.

        This backward-compatible method is not the review-authorized deletion path.
        """
        return [self._delete_path(Path(item), expected_sha256) for item, expected_sha256 in targets]

    def _delete_path(
        self,
        source: Path,
        expected_sha256: str,
        *,
        survivor: Path | None = None,
    ) -> TrashResult:
        if not source.is_file():
            return TrashResult(source, False, error="source file does not exist")
        try:
            actual_sha256 = self.hasher(source)
        except OSError as exc:
            return TrashResult(source, False, error=str(exc))
        if actual_sha256 != expected_sha256:
            return TrashResult(source, False, error="file changed since indexing; not deleted")
        try:
            source_size = source.stat().st_size
        except OSError as exc:
            return TrashResult(source, False, error=str(exc))

        original = source.resolve()
        if survivor is not None and not survivor.is_file():
            return TrashResult(source, False, error="keep copy is missing from disk")
        try:
            destination = self.trash_fn(source)
        except OSError as exc:
            return TrashResult(source, False, error=str(exc))
        except Exception as exc:
            return TrashResult(
                source,
                False,
                error=(
                    f"recycle failed unexpectedly: {exc}; this file's state should be verified"
                ),
            )

        # Windows production backend must return an exact path; injected backends
        # may still return None (legacy). A Windows default that returns None is
        # treated as failure so undo is never logged without a destination.
        if destination is None and self.trash_backend == WINDOWS_TRASH_BACKEND:
            return TrashResult(
                source,
                False,
                error=(
                    "Windows trash backend returned no exact recycled path; "
                    "file may be in Recycle Bin but undo destination is unknown"
                ),
            )

        trashed_to = Path(destination).resolve() if destination is not None else None
        try:
            self._write_log(original, expected_sha256, trashed_to, source_size=source_size)
        except OSError as exc:
            return TrashResult(
                source,
                True,
                trashed_to,
                error=(
                    f"file is in the Recycle Bin but has no Undo Delete entry: {exc}"
                ),
                undo_logged=False,
            )
        return TrashResult(source, True, trashed_to)

    def delete_groups(
        self,
        groups: Iterable[DeleteGroup],
        *,
        roots: Iterable[str | Path],
        on_result: Callable[[TrashResult], None] | None = None,
    ) -> list[TrashResult]:
        """Delete duplicate targets only after group-wide safety checks pass."""
        root_tuple = tuple(roots)
        results: list[TrashResult] = []
        for group in groups:
            reason: str | None = None
            if not root_tuple:
                reason = "no folders are currently added"
            elif not group.survivor.is_file():
                reason = "keep copy is missing from disk"
            else:
                try:
                    survivor_sha256 = self.hasher(group.survivor)
                except OSError as exc:
                    reason = f"keep copy could not be verified: {exc}"
                else:
                    if survivor_sha256 != group.sha256:
                        reason = "keep copy no longer matches the group SHA-256"
            if reason is None and (
                not is_contained(group.survivor, root_tuple)
                or any(not is_contained(target, root_tuple) for target in group.targets)
            ):
                reason = "path is outside every added folder"
            if reason is not None:
                for target in group.targets:
                    result = TrashResult(target, False, error=reason)
                    results.append(result)
                    if on_result is not None:
                        on_result(result)
                continue

            for target in group.targets:
                result = self._delete_path(target, group.sha256, survivor=group.survivor)
                results.append(result)
                if on_result is not None:
                    on_result(result)
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
