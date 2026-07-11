"""Cancelable photo scanning and a Qt worker adapter."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Callable, Iterable

from engine.database.models import PhotoRecord
from engine.duplicates.exact_duplicates import sha256_file
from engine.metadata.exif_reader import capture_datetime, read_exif
from engine.rename.naming import generate_name
from engine.scanner.photo_scanner import scan_photos


class ScanJob:
    def __init__(self, roots: Iterable[str | Path]) -> None:
        self.roots = [Path(root) for root in roots]
        self._cancelled = Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(
        self,
        progress: Callable[[int, int, str], None] | None = None,
        error: Callable[[str, str], None] | None = None,
    ) -> list[PhotoRecord]:
        paths = sorted({path.resolve() for root in self.roots for path in scan_photos(root)}, key=lambda path: str(path).casefold())
        records: list[PhotoRecord] = []
        total = len(paths)
        for index, path in enumerate(paths, 1):
            if self._cancelled.is_set():
                break
            if progress:
                progress(index, total, str(path))
            try:
                record = self._read_record(path)
            except (OSError, ValueError) as exc:
                if error:
                    error(str(path), str(exc))
                continue
            records.append(record)
        return records

    @staticmethod
    def _read_record(path: Path) -> PhotoRecord:
        from PIL import Image, ImageOps, UnidentifiedImageError

        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                oriented = ImageOps.exif_transpose(image)
                width, height = oriented.size
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("Unsupported or corrupted image") from exc
        stat = path.stat()
        captured = capture_datetime(path)
        date_source = "EXIF" if captured else "File modified"
        effective = captured or datetime.fromtimestamp(stat.st_mtime)
        exif = read_exif(path)
        digest = sha256_file(path)
        return PhotoRecord(
            path=str(path), sha256=digest, size=stat.st_size, captured_at=effective.isoformat(timespec="seconds"),
            width=width, height=height, modified_at=stat.st_mtime,
            proposed_name=generate_name(path.name, path.parent.name, effective, digest), date_source=date_source,
            camera_make=str(exif.get("Make", "")).strip() or None, camera_model=str(exif.get("Model", "")).strip() or None,
        )


try:
    from PySide6.QtCore import QObject, Signal, Slot

    class ScanWorker(QObject):
        progress = Signal(int, int, str)
        file_error = Signal(str, str)
        finished = Signal(list, bool)

        def __init__(self, roots: Iterable[str | Path]) -> None:
            super().__init__()
            self.job = ScanJob(roots)

        @Slot()
        def run(self) -> None:
            records = self.job.run(self.progress.emit, self.file_error.emit)
            self.finished.emit(records, self.job._cancelled.is_set())

        @Slot()
        def cancel(self) -> None:
            self.job.cancel()
except ImportError:  # Allows controller tests before optional desktop dependencies are installed.
    ScanWorker = None  # type: ignore[assignment]

