"""Restore files from locations recorded in deletion logs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class UndoDeleteResult:
    current: Path | None
    restored: Path
    undone: bool
    error: str | None = None


class UndoDeleteService:
    def __init__(self, deletion_log: str | Path) -> None:
        self.deletion_log = Path(deletion_log)

    def restore_all(self) -> list[UndoDeleteResult]:
        if not self.deletion_log.is_file():
            return []
        entries = [
            json.loads(line)
            for line in self.deletion_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        results: list[UndoDeleteResult] = []
        remaining: list[dict[str, object]] = []
        for entry in reversed(entries):
            restored = Path(str(entry["original_path"]))
            trashed_value = entry.get("trashed_to")
            current = Path(str(trashed_value)) if trashed_value is not None else None
            if current is None or not current.is_file():
                results.append(
                    UndoDeleteResult(current, restored, False, "trashed file does not exist; cannot restore")
                )
                remaining.append(entry)
                continue
            if restored.exists():
                results.append(
                    UndoDeleteResult(
                        current,
                        restored,
                        False,
                        "restore target already exists; overwrite refused",
                    )
                )
                remaining.append(entry)
                continue
            try:
                restored.parent.mkdir(parents=True, exist_ok=True)
                current.rename(restored)
            except OSError as exc:
                results.append(UndoDeleteResult(current, restored, False, str(exc)))
                remaining.append(entry)
            else:
                results.append(UndoDeleteResult(current, restored, True))

        remaining.reverse()
        content = "".join(json.dumps(entry) + "\n" for entry in remaining)
        self.deletion_log.write_text(content, encoding="utf-8")
        return results
