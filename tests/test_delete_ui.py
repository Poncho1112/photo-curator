from pathlib import Path

import pytest


PySide6 = pytest.importorskip("PySide6", exc_type=ImportError)
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMessageBox

from app.controllers.library_controller import DeleteReviewItem, LibraryController
from app.paths import AppPaths
from app.ui.main_window import MainWindow
from app.views.delete_review import DeleteReviewDialog
from engine.database.models import PhotoRecord
from engine.database.repository import PhotoRepository
from engine.delete.delete_service import TrashResult
from engine.delete.undo_delete_service import UndoDeleteResult


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _window(tmp_path, records):
    settings_file = tmp_path / "settings" / "photo-curator.ini"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings = QSettings(
        str(settings_file),
        QSettings.Format.IniFormat,
    )
    settings.clear()
    repository = PhotoRepository(tmp_path / "catalog.sqlite3")
    for record in records:
        repository.insert(record)
    controller = LibraryController(repository, AppPaths.from_root(tmp_path / "data"))
    return MainWindow(controller, settings=settings), controller


def _review_items(tmp_path):
    first_keep = PhotoRecord(str(tmp_path / "keep-one.jpg"), "a" * 64, 2 * 1024 * 1024, id=1)
    first_delete = PhotoRecord(str(tmp_path / "delete-one.jpg"), "a" * 64, 1024 * 1024, id=2)
    second_keep = PhotoRecord(str(tmp_path / "keep-two.jpg"), "b" * 64, 3 * 1024 * 1024, id=3)
    second_delete_a = PhotoRecord(str(tmp_path / "delete-two.jpg"), "b" * 64, 1024 * 1024, id=4)
    second_delete_b = PhotoRecord(str(tmp_path / "delete-three.jpg"), "b" * 64, 2 * 1024 * 1024, id=5)
    return [
        DeleteReviewItem("group-a", first_keep, (first_delete,), first_delete.size),
        DeleteReviewItem(
            "group-b",
            second_keep,
            (second_delete_a, second_delete_b),
            second_delete_a.size + second_delete_b.size,
        ),
    ]


def test_delete_and_undo_actions_exist_and_are_connected(tmp_path, qt_app, monkeypatch):
    window, controller = _window(tmp_path, [])
    calls = {"review": 0, "question": 0}

    def empty_review():
        calls["review"] += 1
        return []

    def reject_undo(*args):
        calls["question"] += 1
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(controller, "delete_review", empty_review)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", reject_undo)

    assert window.delete_duplicates_action.text() == "Delete Duplicates…"
    assert window.undo_delete_action.text() == "Undo Delete"
    window.delete_duplicates_action.trigger()
    window.undo_delete_action.trigger()
    assert calls == {"review": 1, "question": 1}

    window.close()
    controller.repository.close()


def test_delete_duplicates_flow_executes_review_and_refreshes(tmp_path, qt_app, monkeypatch):
    window, controller = _window(tmp_path, [])
    review = _review_items(tmp_path)
    deleted = []
    summaries = []
    refreshes = []

    monkeypatch.setattr(controller, "delete_review", lambda: review)
    monkeypatch.setattr(
        "app.ui.main_window.DeleteReviewDialog.exec",
        lambda self: QDialog.DialogCode.Accepted,
    )

    def delete_duplicates(received_review):
        deleted.append(received_review)
        return [
            TrashResult(Path(review[0].to_delete[0].path), True),
            TrashResult(
                Path(review[1].to_delete[0].path),
                False,
                error="file changed since indexing; not deleted",
            ),
            TrashResult(Path(review[1].to_delete[1].path), True),
        ]

    monkeypatch.setattr(controller, "delete_duplicates", delete_duplicates)
    monkeypatch.setattr(window, "refresh", lambda: refreshes.append(True))
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda parent, title, message: summaries.append((title, message)),
    )

    window.delete_duplicates_flow()

    assert deleted == [review]
    assert refreshes == [True]
    assert summaries
    assert "Moved to Recycle Bin: 2" in summaries[0][1]
    assert "Skipped: 1" in summaries[0][1]
    assert "changed since indexing" in summaries[0][1]

    window.close()
    controller.repository.close()


def test_delete_duplicates_flow_with_no_duplicates_only_informs(tmp_path, qt_app, monkeypatch):
    window, controller = _window(tmp_path, [])
    messages = []

    monkeypatch.setattr(controller, "delete_review", lambda: [])
    monkeypatch.setattr(
        controller,
        "delete_duplicates",
        lambda review: pytest.fail("delete_duplicates must not run without a review"),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda parent, title, message: messages.append((title, message)),
    )

    window.delete_duplicates_flow()

    assert len(messages) == 1
    assert "No exact duplicates were found" in messages[0][1]

    window.close()
    controller.repository.close()


def test_undo_delete_flow_restores_after_confirmation(tmp_path, qt_app, monkeypatch):
    window, controller = _window(tmp_path, [])
    calls = []
    summaries = []
    refreshes = []

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args: QMessageBox.StandardButton.Yes,
    )

    def undo_delete():
        calls.append(True)
        return [
            UndoDeleteResult(Path("trash/copy.jpg"), Path("photos/copy.jpg"), True),
        ]

    monkeypatch.setattr(controller, "undo_delete", undo_delete)
    monkeypatch.setattr(window, "refresh", lambda: refreshes.append(True))
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda parent, title, message: summaries.append((title, message)),
    )

    window.undo_delete_flow()

    assert calls == [True]
    assert refreshes == [True]
    assert "Restored: 1" in summaries[0][1]

    window.close()
    controller.repository.close()


def test_delete_review_dialog_renders_every_file_and_enables_delete(tmp_path, qt_app):
    review = _review_items(tmp_path)
    dialog = DeleteReviewDialog(
        review,
        sum(item.reclaimable_bytes for item in review),
    )

    assert dialog.table.rowCount() == 5
    assert [dialog.table.item(row, 1).text() for row in range(5)] == [
        "KEEP",
        "DELETE",
        "KEEP",
        "DELETE",
        "DELETE",
    ]
    ok_button = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok_button.isEnabled()
    assert ok_button.text() == "Move 3 copies to Recycle Bin"

    dialog.close()
