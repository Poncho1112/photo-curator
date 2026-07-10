"""Restore names recorded by :mod:`engine.rename.rename_service`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class UndoResult:
    current: Path
    restored: Path
    undone: bool
    error: str | None = None


class UndoService:
    def __init__(self, undo_log: str | Path) -> None:
        self.undo_log = Path(undo_log)

    def restore_all(self) -> list[UndoResult]:
        if not self.undo_log.is_file():
            return []
        entries = [json.loads(line) for line in self.undo_log.read_text(encoding="utf-8").splitlines() if line.strip()]
        results: list[UndoResult] = []
        remaining: list[dict[str, str]] = []
        for entry in reversed(entries):
            restored, current = Path(entry["source"]), Path(entry["target"])
            if not current.is_file():
                results.append(UndoResult(current, restored, False, "renamed file does not exist"))
                remaining.append(entry)
                continue
            if restored.exists():
                results.append(UndoResult(current, restored, False, "restore target already exists; overwrite refused"))
                remaining.append(entry)
                continue
            try:
                current.rename(restored)
            except OSError as exc:
                results.append(UndoResult(current, restored, False, str(exc)))
                remaining.append(entry)
            else:
                results.append(UndoResult(current, restored, True))
        remaining.reverse()
        content = "".join(json.dumps(entry) + "\n" for entry in remaining)
        self.undo_log.write_text(content, encoding="utf-8")
        return results

