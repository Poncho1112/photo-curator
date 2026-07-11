"""Tests for visible-first thumbnail loading and bounded cache storage."""

from __future__ import annotations

import os

import pytest

from engine.thumbnails.thumbnail_service import enforce_cache_limit


def _cache_files(directory, sizes):
    files = []
    for index, size in enumerate(sizes):
        path = directory / f"thumbnail-{index}.jpg"
        path.write_bytes(b"x" * size)
        timestamp = 1_700_000_000 + index
        os.utime(path, (timestamp, timestamp))
        files.append(path)
    return files


def test_cache_eviction_removes_oldest_files_and_stays_contained(tmp_path):
    directory = tmp_path / "thumbnails"
    directory.mkdir()
    files = _cache_files(directory, [40, 40, 40, 40])
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")

    enforce_cache_limit(directory, 90)

    assert not files[0].exists()
    assert not files[1].exists()
    assert files[2].exists()
    assert files[3].exists()
    assert sum(path.stat().st_size for path in directory.iterdir()) <= 90
    assert outside.read_bytes() == b"outside"


def test_cache_under_limit_deletes_nothing(tmp_path):
    directory = tmp_path / "thumbnails"
    directory.mkdir()
    files = _cache_files(directory, [10, 20, 30])

    enforce_cache_limit(directory, 100)

    assert all(path.exists() for path in files)


def test_cache_eviction_rejects_an_arbitrary_directory(tmp_path):
    outside = tmp_path / "not-the-thumbnail-cache"
    outside.mkdir()
    file = outside / "keep.jpg"
    file.write_bytes(b"keep")

    with pytest.raises(ValueError, match="named 'thumbnails'"):
        enforce_cache_limit(outside, 0)

    assert file.exists()


PySide6 = pytest.importorskip("PySide6", exc_type=ImportError)
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from app.controllers.library_controller import LibraryController
from app.paths import AppPaths
from app.ui.main_window import MainWindow
from app.workers.thumbnail_worker import ThumbnailWorker as RealThumbnailWorker
from engine.database.models import PhotoRecord
from engine.database.repository import PhotoRepository


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_visible_items_are_thumbnail_request_prefix(tmp_path, qt_app, monkeypatch):
    records = [
        PhotoRecord(str(tmp_path / f"photo-{index}.jpg"), f"{index:064x}", 5, id=index + 1, status="missing")
        for index in range(36)
    ]
    paths = AppPaths.from_root(tmp_path / "app-data")
    repository = PhotoRepository(paths.database)
    for record in records:
        repository.insert(record)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(LibraryController(repository, paths), settings=settings)
    window.resize(520, 420)
    window.show()
    qt_app.processEvents()

    for record in records:
        (tmp_path / f"photo-{record.id - 1}.jpg").write_bytes(b"photo")
    viewport_rect = window.grid.viewport().rect()
    visible_ids = [
        int(window.grid.item(index).data(Qt.ItemDataRole.UserRole))
        for index in range(window.grid.count())
        if window.grid.visualItemRect(window.grid.item(index)).intersects(viewport_rect)
    ]
    captured = []

    def capturing_worker(requests):
        captured.extend(requests)
        return RealThumbnailWorker(requests)

    monkeypatch.setattr("app.ui.main_window.ThumbnailWorker", capturing_worker)
    window._load_thumbnails(records)

    requested_ids = [request[0] for request in captured]
    assert visible_ids
    assert len(visible_ids) < len(records)
    assert requested_ids[:len(visible_ids)] == visible_ids
    assert set(requested_ids) == {record.id for record in records}

    window.close()
    repository.close()
