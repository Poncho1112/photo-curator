"""Qt worker for cached thumbnail generation."""

from __future__ import annotations

from pathlib import Path

from engine.thumbnails.thumbnail_service import create_thumbnail

try:
    from PySide6.QtCore import QObject, Signal, Slot

    class ThumbnailWorker(QObject):
        ready = Signal(int, str)
        failed = Signal(int, str)
        finished = Signal()

        def __init__(self, requests: list[tuple[int, str, str]]) -> None:
            super().__init__()
            self.requests = requests

        @Slot()
        def run(self) -> None:
            for photo_id, source, target in self.requests:
                try:
                    if not Path(target).is_file():
                        create_thumbnail(source, target)
                except (OSError, ValueError) as exc:
                    self.failed.emit(photo_id, str(exc))
                else:
                    self.ready.emit(photo_id, target)
            self.finished.emit()
except ImportError:
    ThumbnailWorker = None  # type: ignore[assignment]

