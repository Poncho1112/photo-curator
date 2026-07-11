"""Qt worker for cached thumbnail generation."""

from __future__ import annotations

from pathlib import Path
from threading import Event

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
            self._cancel_requested = Event()

        def request_cancel(self) -> None:
            """Request a cooperative stop after the current thumbnail finishes.

            This method is safe to call directly from the GUI thread while
            ``run`` is executing in a worker thread. A queued Qt slot cannot be
            used for cancellation here because the worker's event loop is busy
            for the duration of the synchronous generation loop.
            """
            self._cancel_requested.set()

        # Keep a concise alias for callers that already model workers with a
        # cancel method.
        cancel = request_cancel

        @Slot()
        def run(self) -> None:
            try:
                for photo_id, source, target in self.requests:
                    if self._cancel_requested.is_set():
                        break
                    try:
                        if not Path(target).is_file():
                            create_thumbnail(source, target)
                    except (OSError, ValueError) as exc:
                        self.failed.emit(photo_id, str(exc))
                    else:
                        self.ready.emit(photo_id, target)
            finally:
                self.finished.emit()
except ImportError:
    ThumbnailWorker = None  # type: ignore[assignment]
