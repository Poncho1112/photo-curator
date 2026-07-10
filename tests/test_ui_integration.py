from pathlib import Path

import pytest


PySide6 = pytest.importorskip("PySide6", exc_type=ImportError)
from PySide6.QtWidgets import QApplication

from app.views.photo_preview import PhotoPreview
from engine.database.models import PhotoRecord


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_missing_preview_file_is_handled_without_dialog(tmp_path, qt_app):
    preview = PhotoPreview()
    preview.show_record(PhotoRecord(str(tmp_path / "missing.jpg"), "a" * 64, 0, status="missing"))
    assert preview.image.text() == "File is missing"
    assert not preview.open_button.isEnabled()


def test_preview_displays_service_record_metadata(tmp_path, qt_app):
    preview = PhotoPreview()
    record = PhotoRecord(str(tmp_path / "missing.jpg"), "abcdef12" + "0" * 56, 1024, "2026-07-10", 800, 600, proposed_name="proposed.jpg", camera_make="Canon", camera_model="R5", status="missing")
    preview.show_record(record)
    assert preview.metadata.labels["Proposed"].text() == "proposed.jpg"
    assert preview.metadata.labels["Camera"].text() == "Canon R5"
