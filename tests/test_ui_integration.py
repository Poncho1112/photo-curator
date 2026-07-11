from pathlib import Path

import pytest


PySide6 = pytest.importorskip("PySide6", exc_type=ImportError)
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView

from app.controllers.library_controller import LibraryController
from app.paths import AppPaths
from app.ui.main_window import THEME_KEY, THUMBNAIL_SIZE_KEY, MainWindow
from app.views.photo_grid import PhotoGrid, THUMBNAIL_SIZES
from app.views.photo_preview import PhotoPreview
from app.widgets.photo_tile import PhotoTile
from engine.database.models import PhotoRecord
from engine.database.repository import PhotoRepository


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


def _grid_records(tmp_path):
    first, second, third = (tmp_path / name for name in ("first.jpg", "second.jpg", "third.jpg"))
    for path in (first, second, third):
        path.write_bytes(b"photo")
    return [
        PhotoRecord(str(first), "a" * 64, 5, id=1),
        PhotoRecord(str(second), "b" * 64, 5, id=2, duplicate_group="bbbbbbbb"),
        PhotoRecord(str(third), "c" * 64, 5, id=3),
    ]


def test_grid_uses_large_checkbox_tiles_and_separate_selection(tmp_path, qt_app):
    grid = PhotoGrid()
    grid.populate(_grid_records(tmp_path), {2})
    first_tile = grid.itemWidget(grid.item(0))
    second_tile = grid.itemWidget(grid.item(1))
    assert isinstance(first_tile, PhotoTile)
    assert first_tile.image.width() == THUMBNAIL_SIZES["Medium"]
    assert not first_tile.rename_checkbox.isChecked()
    assert second_tile.rename_checkbox.isChecked()

    grid.item(0).setSelected(True)
    assert grid.selected_photo_ids() == {1}
    assert not first_tile.rename_checkbox.isChecked()


def test_checkbox_emits_explicit_rename_state(tmp_path, qt_app):
    grid = PhotoGrid()
    grid.populate(_grid_records(tmp_path), set())
    changes = []
    grid.rename_toggled.connect(lambda photo_id, selected: changes.append((photo_id, selected)))
    tile = grid.itemWidget(grid.item(0))
    tile.rename_checkbox.setChecked(True)
    assert changes == [(1, True)]


def test_ctrl_and_shift_selection_are_supported(tmp_path, qt_app):
    grid = PhotoGrid()
    grid.populate(_grid_records(tmp_path), set())
    grid._tile_clicked(1, Qt.KeyboardModifier.NoModifier)
    grid._tile_clicked(3, Qt.KeyboardModifier.ShiftModifier)
    assert grid.selected_photo_ids() == {1, 2, 3}
    grid._tile_clicked(2, Qt.KeyboardModifier.ControlModifier)
    assert grid.selected_photo_ids() == {1, 3}
    assert grid.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection


def test_thumbnail_size_presets_resize_tiles(tmp_path, qt_app):
    grid = PhotoGrid()
    grid.set_thumbnail_size("Large")
    grid.populate(_grid_records(tmp_path), set())
    tile = grid.itemWidget(grid.item(0))
    assert tile.image.width() == THUMBNAIL_SIZES["Large"]
    grid.set_thumbnail_size("Small")
    assert grid.thumbnail_width == THUMBNAIL_SIZES["Small"]


def _settings_file(tmp_path):
    path = tmp_path / "settings" / "photo-curator.ini"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _window(tmp_path, records, *, clear_settings=True, settings=None):
    settings = settings if settings is not None else QSettings(
        str(_settings_file(tmp_path)), QSettings.Format.IniFormat
    )
    if clear_settings:
        settings.clear()
        settings.sync()
        assert settings.status() == QSettings.Status.NoError
    repository = PhotoRepository(tmp_path / f"catalog-{len(list(tmp_path.glob('catalog-*')))}.sqlite3")
    for record in records:
        repository.insert(record)
    controller = LibraryController(repository, AppPaths.from_root(tmp_path / "data"))
    window = MainWindow(controller, settings=settings)
    return window, controller


def test_rename_checkbox_updates_controller_without_ui_selection(tmp_path, qt_app):
    window, controller = _window(tmp_path, [PhotoRecord(str(tmp_path / "missing.jpg"), "a" * 64, 0, status="missing")])
    tile = window.grid.itemWidget(window.grid.item(0))
    tile.rename_checkbox.setChecked(True)
    assert controller.rename_selection == {controller.records[0].id}
    assert window.grid.selected_photo_ids() == set()
    window.close()
    controller.repository.close()


def test_mark_and_remove_selected_for_rename_actions(tmp_path, qt_app):
    records = [PhotoRecord(str(tmp_path / name), character * 64, 0, status="missing") for name, character in (("one.jpg", "a"), ("two.jpg", "b"))]
    window, controller = _window(tmp_path, records)
    window.grid.item(0).setSelected(True)
    window.grid.item(1).setSelected(True)
    window.mark_action.trigger()
    assert len(controller.rename_selection) == 2
    assert window.rename_action.text() == "Rename Selected (2)"
    window.remove_rename_action.trigger()
    assert controller.rename_selection == set()
    assert not window.rename_action.isEnabled()
    window.close()
    controller.repository.close()


def test_status_summary_and_filter_counts_update(tmp_path, qt_app):
    records = [PhotoRecord(str(tmp_path / "beach.jpg"), "a" * 64, 0, status="missing"), PhotoRecord(str(tmp_path / "work.jpg"), "b" * 64, 0, status="missing")]
    window, controller = _window(tmp_path, records)
    window.grid.item(0).setSelected(True)
    assert "2 photos | 1 selected" in window.summary.text()
    window.search.setText("beach")
    window.apply_filters()
    assert window.summary.text().startswith("1 photos")
    window.close()
    controller.repository.close()


def test_context_target_uses_current_multi_selection(tmp_path, qt_app):
    grid = PhotoGrid()
    grid.populate(_grid_records(tmp_path), set())
    grid.item(0).setSelected(True)
    grid.item(1).setSelected(True)
    assert grid.context_target_ids(1) == [1, 2]
    assert grid.context_target_ids(3) == [3]


def test_required_keyboard_shortcuts_are_registered(tmp_path, qt_app):
    window, controller = _window(tmp_path, [])
    assert window.add_folder_action.shortcut().toString() == "Ctrl+O"
    assert window.scan_action.shortcut().toString() == "F5"
    assert window.rename_action.shortcut().toString() == "Ctrl+R"
    assert window.remove_rename_action.shortcut().toString() == "Ctrl+Shift+R"
    assert window.undo_action.shortcut().toString() == "Ctrl+Z"
    assert window.export_action.shortcut().toString() == "Ctrl+E"
    assert window.select_all_action.shortcut().toString() == "Ctrl+A"
    window.close()
    controller.repository.close()


@pytest.mark.parametrize("theme", ["Dark", "Light"])
def test_theme_persists_through_qsettings(tmp_path, qt_app, theme):
    first, first_controller = _window(tmp_path, [])
    first.set_theme(theme)
    assert first.settings.status() == QSettings.Status.NoError
    first.close()
    first_controller.repository.close()

    second_settings = QSettings(str(_settings_file(tmp_path)), QSettings.Format.IniFormat)
    second, second_controller = _window(tmp_path, [], clear_settings=False, settings=second_settings)
    assert second.settings.status() == QSettings.Status.NoError
    assert second.settings.value(THEME_KEY) == theme
    assert second.active_theme == theme
    assert second.theme_actions[theme].isChecked()
    second.close()
    second_controller.repository.close()


def test_thumbnail_size_persists_through_qsettings(tmp_path, qt_app):
    first, first_controller = _window(tmp_path, [])
    first.change_thumbnail_size("Large")
    assert first.settings.status() == QSettings.Status.NoError
    first.close()
    first_controller.repository.close()

    second_settings = QSettings(str(_settings_file(tmp_path)), QSettings.Format.IniFormat)
    second, second_controller = _window(tmp_path, [], clear_settings=False, settings=second_settings)
    assert second.settings.status() == QSettings.Status.NoError
    assert second.settings.value(THUMBNAIL_SIZE_KEY) == "Large"
    assert second.thumbnail_size.currentText() == "Large"
    assert second.grid.thumbnail_width == THUMBNAIL_SIZES["Large"]
    second.close()
    second_controller.repository.close()


def test_invalid_stored_theme_falls_back_to_system_without_overwriting(tmp_path, qt_app):
    settings = QSettings(str(_settings_file(tmp_path)), QSettings.Format.IniFormat)
    settings.clear()
    settings.setValue(THEME_KEY, "Sepia")
    settings.sync()
    assert settings.status() == QSettings.Status.NoError

    fresh_settings = QSettings(str(_settings_file(tmp_path)), QSettings.Format.IniFormat)
    window, controller = _window(tmp_path, [], clear_settings=False, settings=fresh_settings)
    assert window.active_theme == "System"
    assert window.theme_actions["System"].isChecked()
    assert window.settings.value(THEME_KEY) == "Sepia"
    window.close()
    controller.repository.close()


def test_separate_qsettings_instances_share_same_ini_file_on_windows(tmp_path):
    settings_file = _settings_file(tmp_path)
    writer = QSettings(str(settings_file), QSettings.Format.IniFormat)
    writer.setValue(THEME_KEY, "Dark")
    writer.setValue(THUMBNAIL_SIZE_KEY, "Large")
    writer.sync()
    assert writer.status() == QSettings.Status.NoError

    reader = QSettings(str(settings_file), QSettings.Format.IniFormat)
    assert reader.value(THEME_KEY) == "Dark"
    assert reader.value(THUMBNAIL_SIZE_KEY) == "Large"
    assert reader.status() == QSettings.Status.NoError


def test_missing_metadata_state_and_double_click_preview(tmp_path, qt_app, monkeypatch):
    missing = tmp_path / "missing.jpg"
    window, controller = _window(tmp_path, [PhotoRecord(str(missing), "a" * 64, 0, status="missing")])
    monkeypatch.setattr("app.views.photo_preview._open_path", lambda path: pytest.fail("OS viewer must not open"))
    window.grid._activate_item(window.grid.item(0))
    assert window.preview.record.id == controller.records[0].id
    assert window.preview.image.text() == "File is missing"
    assert window.preview.metadata.labels["Status"].text() == "missing"
    assert not window.preview.open_button.isEnabled()
    window.close()
    controller.repository.close()
