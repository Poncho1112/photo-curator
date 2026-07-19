"""Restore files from locations recorded in deletion logs."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from engine.duplicates.exact_duplicates import sha256_file

LegacyLocator = Callable[[dict[str, object]], Path]


@dataclass(frozen=True, slots=True)
class UndoDeleteResult:
    current: Path | None
    restored: Path
    undone: bool
    error: str | None = None


def _default_legacy_locator() -> LegacyLocator | None:
    """Windows-only default: locate recycled files via $I/$R metadata."""
    if os.name != "nt":
        return None
    from engine.delete.windows_recycle_bin import make_legacy_locator

    return make_legacy_locator()


class UndoDeleteService:
    def __init__(
        self,
        deletion_log: str | Path,
        *,
        legacy_locator: LegacyLocator | None = None,
        hasher: Callable[[Path], str] | None = None,
        enable_default_legacy_locator: bool = True,
    ) -> None:
        self.deletion_log = Path(deletion_log)
        if legacy_locator is not None:
            self.legacy_locator: LegacyLocator | None = legacy_locator
        elif enable_default_legacy_locator:
            self.legacy_locator = _default_legacy_locator()
        else:
            self.legacy_locator = None
        self.hasher = hasher or sha256_file

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
            result, keep = self._restore_entry(entry)
            results.append(result)
            if keep:
                remaining.append(entry)

        remaining.reverse()
        content = "".join(json.dumps(entry) + "\n" for entry in remaining)
        self.deletion_log.write_text(content, encoding="utf-8")
        return results

    def _resolve_current(self, entry: dict[str, object]) -> tuple[Path | None, str | None]:
        """Resolve the trashed file path from the log entry or legacy locator.

        Successful historical logs without ``trashed_to`` are still treated as
        trashed: DeleteService only appends after a successful trash call.
        """
        trashed_value = entry.get("trashed_to")
        if trashed_value is not None and str(trashed_value).strip() != "":
            current = Path(str(trashed_value))
            if current.is_file():
                return current, None
            return current, "trashed file does not exist; cannot restore"

        # Legacy / null destination: optional locator (Windows $I/$R by default).
        if self.legacy_locator is None:
            return None, (
                "trashed_to missing from deletion log and no legacy locator configured; "
                "cannot restore"
            )
        try:
            located = self.legacy_locator(entry)
        except OSError as exc:
            return None, str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            return None, f"legacy recycle-bin lookup failed: {exc}"
        if located is None:
            return None, "legacy recycle-bin lookup returned no path; cannot restore"
        current = Path(located)
        if not current.is_file():
            return current, "trashed file does not exist; cannot restore"
        return current, None

    def _restore_entry(self, entry: dict[str, object]) -> tuple[UndoDeleteResult, bool]:
        restored = Path(str(entry["original_path"]))
        current, resolve_error = self._resolve_current(entry)
        if resolve_error is not None:
            return UndoDeleteResult(current, restored, False, resolve_error), True

        assert current is not None

        expected_sha = entry.get("sha256")
        if expected_sha is not None:
            try:
                actual_sha = self.hasher(current)
            except OSError as exc:
                return UndoDeleteResult(current, restored, False, str(exc)), True
            if actual_sha != str(expected_sha):
                return (
                    UndoDeleteResult(
                        current,
                        restored,
                        False,
                        "trashed file SHA-256 does not match deletion log; restore refused",
                    ),
                    True,
                )

        if restored.exists():
            return (
                UndoDeleteResult(
                    current,
                    restored,
                    False,
                    "restore target already exists; overwrite refused",
                ),
                True,
            )

        try:
            restored.parent.mkdir(parents=True, exist_ok=True)
            current.rename(restored)
        except OSError as exc:
            return UndoDeleteResult(current, restored, False, str(exc)), True
        return UndoDeleteResult(current, restored, True), False
