from __future__ import annotations

import pytest


pytest.importorskip("PySide6", exc_type=ImportError)

from app.workers import thumbnail_worker as worker_module


def test_thumbnail_worker_cancel_stops_between_requests(tmp_path, monkeypatch):
    generated: list[str] = []
    worker = worker_module.ThumbnailWorker(
        [
            (1, "one.jpg", str(tmp_path / "one.png")),
            (2, "two.jpg", str(tmp_path / "two.png")),
        ]
    )

    def create_and_cancel(source: str, target: str) -> None:
        generated.append(source)
        worker.request_cancel()

    monkeypatch.setattr(worker_module, "create_thumbnail", create_and_cancel)
    ready: list[tuple[int, str]] = []
    finished: list[bool] = []
    worker.ready.connect(lambda photo_id, target: ready.append((photo_id, target)))
    worker.finished.connect(lambda: finished.append(True))

    worker.run()

    assert generated == ["one.jpg"]
    assert ready == [(1, str(tmp_path / "one.png"))]
    assert finished == [True]


def test_thumbnail_worker_cancel_before_run_finishes_without_work(
    tmp_path, monkeypatch
):
    generated: list[str] = []
    worker = worker_module.ThumbnailWorker(
        [(1, "one.jpg", str(tmp_path / "one.png"))]
    )
    monkeypatch.setattr(
        worker_module,
        "create_thumbnail",
        lambda source, target: generated.append(source),
    )
    finished: list[bool] = []
    worker.finished.connect(lambda: finished.append(True))

    worker.cancel()
    worker.run()

    assert generated == []
    assert finished == [True]
