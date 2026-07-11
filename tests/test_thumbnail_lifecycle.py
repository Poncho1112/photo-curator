"""Focused offscreen tests for the main-window thumbnail lifecycle."""

from __future__ import annotations

import pytest


pytest.importorskip("PySide6", exc_type=ImportError)
from PySide6.QtCore import QSettings
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from app.controllers.library_controller import LibraryController
from app.paths import AppPaths
from app.ui.main_window import MainWindow
from engine.database.repository import PhotoRepository


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(tmp_path, qt_app):
    paths = AppPaths.from_root(tmp_path / "app-data")
    repository = PhotoRepository(paths.database)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    instance = MainWindow(LibraryController(repository, paths), settings=settings)
    yield instance
    if not instance._closing:
        instance.close()
    repository.close()


class _Disposable:
    def __init__(self) -> None:
        self.deleted = False

    def deleteLater(self) -> None:
        self.deleted = True


def test_active_thumbnail_batch_queues_refresh_for_latest_visible_records(window, monkeypatch):
    active_thread = _Disposable()
    active_worker = _Disposable()
    window.thumbnail_thread = active_thread
    window.thumbnail_worker = active_worker
    window._visible_records = ["latest-visible-record"]

    window._load_thumbnails(["superseded-record"])

    assert window._thumbnail_refresh_pending
    restarted_with = []
    monkeypatch.setattr(window, "_load_thumbnails", lambda records: restarted_with.append(list(records)))
    window._thumbnail_finished()

    assert active_thread.deleted
    assert active_worker.deleted
    assert restarted_with == [["latest-visible-record"]]
    assert not window._thumbnail_refresh_pending


class _RunningThread:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def isRunning(self) -> bool:
        return True

    def quit(self) -> None:
        self.calls.append("quit")

    def wait(self) -> None:
        self.calls.append("wait")


class _CancellableWorker:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def cancel(self) -> None:
        self.calls.append("cancel")


def test_close_cancels_and_joins_active_thumbnail_thread(window):
    calls: list[str] = []
    window.thumbnail_worker = _CancellableWorker(calls)
    window.thumbnail_thread = _RunningThread(calls)
    window._thumbnail_refresh_pending = True

    window.closeEvent(QCloseEvent())

    assert calls == ["cancel", "quit", "wait"]
    assert window._closing
    assert not window._thumbnail_refresh_pending

