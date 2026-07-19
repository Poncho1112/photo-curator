"""Hash-verified moves to trash with an append-only deletion log."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from engine.delete.trash_providers import select_default_trash_fn
from engine.duplicates.exact_duplicates import sha256_file


@dataclass(frozen=True, slots=True)
class TrashResult:
    source: Path
    trashed: bool
    trashed_to: Path | None = None
    error: str | None = None


class DeleteService:
    def __init__(
        self,
        deletion_log: str | Path,
        trash_fn: Callable[[Path], Path | None] | None = None,
        hasher: Callable[[Path], str] | None = None,
    ) -> None:
        self.deletion_log = Path(deletion_log)
        # select_default_trash_fn() picks a recoverable, platform-native
        # provider (see engine/delete/trash_providers/) so files trashed
        # through the default path are restorable via UndoDeleteService.
        self.trash_fn = trash_fn or select_default_trash_fn()
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

            original = source.resolve()
            try:
                destination = self.trash_fn(source)
            except OSError as exc:
                results.append(TrashResult(source, False, error=str(exc)))
                continue
            trashed_to = Path(destination).resolve() if destination is not None else None
            self._write_log(original, expected_sha256, trashed_to)
            results.append(TrashResult(source, True, trashed_to))
        return results

    def _write_log(self, source: Path, sha256: str, trashed_to: Path | None) -> None:
        self.deletion_log.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps(
            {
                "original_path": str(source),
                "sha256": sha256,
                "trashed_to": str(trashed_to) if trashed_to is not None else None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        with self.deletion_log.open("a", encoding="utf-8") as log:
            log.write(entry + "\n")
            log.flush()
            os.fsync(log.fileno())
