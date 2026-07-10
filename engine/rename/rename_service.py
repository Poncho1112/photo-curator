"""Safe selected-file rename operations with an append-only undo log."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable

from engine.duplicates.exact_duplicates import sha256_file
from engine.metadata.exif_reader import capture_datetime

from .naming import generate_name


@dataclass(frozen=True, slots=True)
class RenameResult:
    source: Path
    target: Path | None
    renamed: bool
    error: str | None = None


def _default_date(path: Path) -> date:
    captured = capture_datetime(path)
    return captured.date() if captured else datetime.fromtimestamp(path.stat().st_mtime).date()


class RenameService:
    def __init__(self, undo_log: str | Path, date_provider: Callable[[Path], date] | None = None) -> None:
        self.undo_log = Path(undo_log)
        self.date_provider = date_provider or _default_date

    def rename_selected(self, selected: Iterable[str | Path]) -> list[RenameResult]:
        results: list[RenameResult] = []
        for item in selected:
            source = Path(item)
            if not source.is_file():
                results.append(RenameResult(source, None, False, "source file does not exist"))
                continue
            target = source.with_name(
                generate_name(source.name, source.parent.name, self.date_provider(source), sha256_file(source))
            )
            if target == source:
                results.append(RenameResult(source, target, False, "source already has the generated name"))
                continue
            if target.exists():
                results.append(RenameResult(source, target, False, "target already exists; overwrite refused"))
                continue
            try:
                self._write_log(source, target)
                source.rename(target)
            except OSError as exc:
                results.append(RenameResult(source, target, False, str(exc)))
            else:
                results.append(RenameResult(source, target, True))
        return results

    def _write_log(self, source: Path, target: Path) -> None:
        self.undo_log.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps({"source": str(source.resolve()), "target": str(target.resolve())})
        with self.undo_log.open("a", encoding="utf-8") as log:
            log.write(entry + "\n")
            log.flush()
            os.fsync(log.fileno())

