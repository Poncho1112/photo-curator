"""Photo Curator main window."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSettings, QThread, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence
from PySide6.QtWidgets import QComboBox, QFileDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QSplitter, QToolBar

from app.controllers.library_controller import LibraryController
from app.views.folder_panel import FolderPanel
from app.views.photo_grid import PhotoGrid
from app.views.photo_preview import PhotoPreview
from app.views.rename_review import RenameReviewDialog
from app.widgets.progress_panel import ProgressPanel
from app.workers.scan_worker import ScanWorker
from app.workers.thumbnail_worker import ThumbnailWorker


log = logging.getLogger(__name__)

SETTINGS_ORGANIZATION = "ClearFusionLab"
SETTINGS_APPLICATION = "PhotoCurator"
THEME_KEY = "theme"
THUMBNAIL_SIZE_KEY = "thumbnailSize"
VALID_THEMES = frozenset({"System", "Light", "Dark"})
VALID_THUMBNAIL_SIZES = frozenset({"Small", "Medium", "Large"})


def create_application_settings(settings_file: str | Path | None = None) -> QSettings:
    """Create Photo Curator's explicit, cross-platform INI settings store."""
    if settings_file is None:
        from app.paths import AppPaths

        settings_file = AppPaths.default().root / "settings.ini"
    path = Path(settings_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    return QSettings(str(path), QSettings.Format.IniFormat)


class MainWindow(QMainWindow):
    def __init__(self, controller: LibraryController, settings: QSettings | None = None) -> None:
        super().__init__()
        self.controller = controller
        self.scan_thread: QThread | None = None
        self.scan_worker = None
        self.thumbnail_thread: QThread | None = None
        self.thumbnail_worker = None
        self._thumbnail_refresh_pending = False
        self._closing = False
        self.scan_errors: list[tuple[str, str]] = []
        self.settings = settings if settings is not None else create_application_settings(
            controller.paths.root / "settings.ini"
        )
        stored_theme = str(self.settings.value(THEME_KEY, ""))
        self.active_theme = stored_theme if stored_theme in VALID_THEMES else "System"
        stored_thumbnail_size = str(self.settings.value(THUMBNAIL_SIZE_KEY, ""))
        self.saved_thumbnail_size = (
            stored_thumbnail_size if stored_thumbnail_size in VALID_THUMBNAIL_SIZES else "Medium"
        )
        self.setWindowTitle("Photo Curator")
        self.resize(1450, 850)
        self._build_ui()
        self._connect()
        self.grid.set_thumbnail_size(self.thumbnail_size.currentText())
        self.size_actions[self.thumbnail_size.currentText()].setChecked(True)
        self.set_theme(self.active_theme, persist=False)
        self.refresh()

    def _build_ui(self) -> None:
        self._create_actions()
        self._build_menus()
        toolbar = QToolBar("Library")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        toolbar.addAction(self.add_folder_action)
        toolbar.addAction(self.scan_action)
        toolbar.addSeparator()
        toolbar.addAction(self.rename_action)
        toolbar.addAction(self.undo_action)
        toolbar.addSeparator()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search filename, path, date, camera, status…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(300)
        toolbar.addWidget(self.search)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel("Thumbnail size:"))
        self.thumbnail_size = QComboBox()
        self.thumbnail_size.addItems(("Small", "Medium", "Large"))
        self.thumbnail_size.setCurrentText(self.saved_thumbnail_size)
        toolbar.addWidget(self.thumbnail_size)

        self.folder_panel = FolderPanel()
        self.grid = PhotoGrid()
        self.preview = PhotoPreview()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.folder_panel)
        splitter.addWidget(self.grid)
        splitter.addWidget(self.preview)
        splitter.setCollapsible(0, True)
        splitter.setSizes([240, 760, 450])
        self.setCentralWidget(splitter)
        self.progress = ProgressPanel()
        self.summary = QLabel()
        self.summary.setObjectName("statusSummary")
        self.statusBar().addPermanentWidget(self.summary, 1)
        self.statusBar().addPermanentWidget(self.progress, 2)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(250)

    def _create_actions(self) -> None:
        self.add_folder_action = QAction("Add Folder", self)
        self.remove_folder_action = QAction("Remove Folder", self)
        self.scan_action = QAction("Scan", self)
        self.cancel_action = QAction("Cancel Scan", self)
        self.cancel_action.setEnabled(False)
        self.export_action = QAction("Export Manifest", self)
        self.rename_action = QAction("Rename Selected (0)", self)
        self.rename_action.setEnabled(False)
        self.undo_action = QAction("Undo Last Batch", self)
        self.select_all_action = QAction("Select All Visible", self)
        self.clear_selection_action = QAction("Clear Selection", self)
        self.mark_action = QAction("Mark for Rename", self)
        self.remove_rename_action = QAction("Remove from Rename", self)
        self.toggle_rename_action = QAction("Toggle Rename Selection", self)
        self.copy_path_action = QAction("Copy Path", self)
        self.copy_filename_action = QAction("Copy Filename", self)
        self.focus_preview_action = QAction("Focus Preview", self)
        for name, action in vars(self).copy().items():
            if name.endswith("_action") and isinstance(action, QAction):
                action.setObjectName(name)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.add_folder_action)
        file_menu.addAction(self.export_action)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close, QKeySequence.StandardKey.Quit)

        library_menu = self.menuBar().addMenu("&Library")
        library_menu.addActions((self.scan_action, self.cancel_action))
        library_menu.addSeparator()
        library_menu.addAction("Show Duplicates", lambda: self._set_filter_checkbox(self.folder_panel.duplicates, True))
        library_menu.addAction("Show Missing", lambda: self._set_filter_checkbox(self.folder_panel.missing, True))

        selection_menu = self.menuBar().addMenu("&Selection")
        selection_menu.addActions((self.select_all_action, self.clear_selection_action))
        selection_menu.addSeparator()
        selection_menu.addActions((self.mark_action, self.remove_rename_action, self.toggle_rename_action))

        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addActions((self.undo_action, self.copy_path_action, self.copy_filename_action))

        view_menu = self.menuBar().addMenu("&View")
        size_menu = view_menu.addMenu("Thumbnail Size")
        self.size_actions: dict[str, QAction] = {}
        size_group = QActionGroup(self)
        for preset in ("Small", "Medium", "Large"):
            action = size_menu.addAction(preset)
            action.setCheckable(True)
            action.setData(preset)
            size_group.addAction(action)
            self.size_actions[preset] = action
            action.triggered.connect(lambda checked=False, value=preset: self.thumbnail_size.setCurrentText(value))
        theme_menu = view_menu.addMenu("Theme")
        self.theme_actions: dict[str, QAction] = {}
        theme_group = QActionGroup(self)
        for theme in ("System", "Light", "Dark"):
            action = theme_menu.addAction(theme)
            action.setCheckable(True)
            action.setData(theme)
            theme_group.addAction(action)
            self.theme_actions[theme] = action
            action.triggered.connect(lambda checked=False, value=theme: self.set_theme(value))
        view_menu.addSeparator()
        view_menu.addAction(self.focus_preview_action)

    def _connect(self) -> None:
        self.add_folder_action.triggered.connect(self.add_folder)
        self.folder_panel.add_button.clicked.connect(self.add_folder)
        self.remove_folder_action.triggered.connect(self.remove_folder)
        self.folder_panel.remove_button.clicked.connect(self.remove_folder)
        self.scan_action.triggered.connect(self.start_scan)
        self.cancel_action.triggered.connect(self.cancel_scan)
        self.export_action.triggered.connect(self.export_manifest)
        self.rename_action.triggered.connect(self.rename_selected)
        self.undo_action.triggered.connect(self.undo_latest)
        self.search.textChanged.connect(lambda: self.debounce.start())
        self.debounce.timeout.connect(self.apply_filters)
        self.folder_panel.filters_changed.connect(self.apply_filters)
        self.grid.photo_activated.connect(self.show_photo)
        self.grid.rename_toggled.connect(self.toggle_rename)
        self.grid.mark_requested.connect(self.mark_selected_for_rename)
        self.grid.remove_requested.connect(self.remove_selected_from_rename)
        self.grid.toggle_requested.connect(self.toggle_selected_for_rename)
        self.grid.context_requested.connect(self.show_context_menu)
        self.grid.itemSelectionChanged.connect(self.update_chrome)
        self.preview.rename_toggled.connect(self.toggle_rename)
        self.thumbnail_size.currentTextChanged.connect(self.change_thumbnail_size)
        self.select_all_action.triggered.connect(self.grid.select_all_visible)
        self.clear_selection_action.triggered.connect(self.grid.clear_ui_selection)
        self.mark_action.triggered.connect(lambda: self.mark_selected_for_rename(sorted(self.grid.selected_photo_ids())))
        self.remove_rename_action.triggered.connect(lambda: self.remove_selected_from_rename(sorted(self.grid.selected_photo_ids())))
        self.toggle_rename_action.triggered.connect(lambda: self.toggle_selected_for_rename(sorted(self.grid.selected_photo_ids())))
        self.copy_path_action.triggered.connect(lambda: self.copy_selected("path"))
        self.copy_filename_action.triggered.connect(lambda: self.copy_selected("filename"))
        self.focus_preview_action.triggered.connect(self.preview.setFocus)
        self._install_shortcuts()

    def _install_shortcuts(self) -> None:
        shortcuts = (
            (self.add_folder_action, QKeySequence.StandardKey.Open),
            (self.scan_action, QKeySequence("F5")),
            (self.export_action, QKeySequence("Ctrl+E")),
            (self.rename_action, QKeySequence("Ctrl+R")),
            (self.remove_rename_action, QKeySequence("Ctrl+Shift+R")),
            (self.undo_action, QKeySequence.StandardKey.Undo),
            (self.select_all_action, QKeySequence.StandardKey.SelectAll),
        )
        for action, shortcut in shortcuts:
            action.setShortcut(shortcut)
        focus_search = QAction(self)
        focus_search.setShortcut(QKeySequence.StandardKey.Find)
        focus_search.triggered.connect(lambda: (self.search.setFocus(), self.search.selectAll()))
        self.addAction(focus_search)
        escape = QAction(self)
        escape.setObjectName("escape_action")
        escape.setShortcut(QKeySequence("Esc"))
        escape.triggered.connect(self.cancel_or_clear_selection)
        self.addAction(escape)

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add Photo Folder")
        if folder and folder not in self.folder_panel.folder_paths():
            self.folder_panel.add_folder(folder)

    def remove_folder(self) -> None:
        for item in self.folder_panel.folders.selectedItems():
            self.folder_panel.folders.takeItem(self.folder_panel.folders.row(item))

    def start_scan(self) -> None:
        roots = self.folder_panel.folder_paths()
        if not roots:
            QMessageBox.information(self, "Choose a folder", "Add at least one copied photo folder before scanning.")
            return
        if ScanWorker is None:
            self._error("Dependencies missing", "PySide6 and Pillow must be installed before scanning.")
            return
        self.scan_errors.clear()
        self.scan_thread = QThread(self)
        self.scan_worker = ScanWorker(roots)
        self.scan_worker.moveToThread(self.scan_thread)
        self.scan_thread.started.connect(self.scan_worker.run)
        self.scan_worker.progress.connect(self.progress.update_progress)
        self.scan_worker.file_error.connect(lambda path, error: self.scan_errors.append((path, error)))
        self.scan_worker.finished.connect(self._scan_finished)
        self.scan_worker.finished.connect(self.scan_thread.quit)
        self.scan_thread.finished.connect(self.scan_worker.deleteLater)
        self.scan_thread.finished.connect(self.scan_thread.deleteLater)
        self.scan_thread.finished.connect(self._scan_thread_finished)
        self.scan_action.setEnabled(False)
        self.cancel_action.setEnabled(True)
        self.statusBar().showMessage("Scanning…")
        self.scan_thread.start()

    def cancel_scan(self) -> None:
        if self.scan_worker:
            self.scan_worker.cancel()
            self.statusBar().showMessage("Cancelling after current file…")

    def cancel_or_clear_selection(self) -> None:
        if self.scan_worker:
            self.cancel_scan()
        else:
            self.grid.clear_ui_selection()

    def _scan_finished(self, records: list, cancelled: bool) -> None:
        try:
            self.controller.index_records(records, self.folder_panel.folder_paths(), complete_scan=not cancelled)
        except Exception as exc:
            log.exception("Could not update catalog")
            self._error("Database error", str(exc))
        self.scan_action.setEnabled(True)
        self.cancel_action.setEnabled(False)
        self.scan_worker = None
        summary = f"Indexed {len(records)} photo(s)"
        if cancelled:
            summary += " before cancellation"
        if self.scan_errors:
            summary += f"; skipped {len(self.scan_errors)} unreadable file(s)"
        self.statusBar().showMessage(summary, 10000)
        self.refresh()

    def _scan_thread_finished(self) -> None:
        self.scan_thread = None

    def apply_filters(self) -> None:
        self.controller.set_filters(
            query=self.search.text(), duplicates_only=self.folder_panel.duplicates.isChecked(),
            missing_only=self.folder_panel.missing.isChecked(), renamed_only=self.folder_panel.renamed.isChecked(),
            selected_only=self.folder_panel.selected.isChecked(),
        )
        self.refresh(load=False)

    def refresh(self, load: bool = True) -> None:
        if load:
            self.controller.load_records()
        records = self.controller.filtered_records()
        self._visible_records = records
        self.grid.populate(records, self.controller.rename_selection)
        self.folder_panel.refresh_counts(self.controller.records)
        self.update_chrome()
        self._load_thumbnails(records)

    def update_chrome(self) -> None:
        records = self._visible_records if hasattr(self, "_visible_records") else self.controller.filtered_records()
        duplicates = sum(bool(record.duplicate_group) for record in records)
        missing = sum(record.status == "missing" or not Path(record.path).is_file() for record in records)
        ui_selected = len(self.grid.selected_photo_ids())
        rename_count = len(self.controller.rename_selection)
        self.summary.setText(
            f"{len(records):,} photos | {ui_selected:,} selected | {rename_count:,} marked for rename | "
            f"{duplicates:,} duplicates | {missing:,} missing"
        )
        self.rename_action.setText(f"Rename Selected ({rename_count})")
        self.rename_action.setEnabled(rename_count > 0)
        has_ui_selection = ui_selected > 0
        for action in (self.mark_action, self.remove_rename_action, self.toggle_rename_action, self.copy_path_action, self.copy_filename_action):
            action.setEnabled(has_ui_selection)

    def _load_thumbnails(self, records) -> None:
        if ThumbnailWorker is None or self._closing:
            return
        if self.thumbnail_thread is not None:
            # Search/filter/size changes can replace the grid while the current
            # batch is still running.  Revisit the latest visible records once
            # that batch has stopped so their placeholders are not stranded.
            self._thumbnail_refresh_pending = True
            return
        requests = []
        for record in records:
            if record.id is not None and Path(record.path).is_file():
                target = self.controller.paths.thumbnails / f"{record.sha256}.jpg"
                requests.append((record.id, record.path, str(target)))
        if not requests:
            return
        self._thumbnail_refresh_pending = False
        self.thumbnail_thread = QThread(self)
        self.thumbnail_worker = ThumbnailWorker(requests)
        self.thumbnail_worker.moveToThread(self.thumbnail_thread)
        self.thumbnail_thread.started.connect(self.thumbnail_worker.run)
        self.thumbnail_worker.ready.connect(self._thumbnail_ready)
        self.thumbnail_worker.failed.connect(lambda photo_id, error: log.warning("Thumbnail %s failed: %s", photo_id, error))
        self.thumbnail_worker.finished.connect(self.thumbnail_thread.quit)
        self.thumbnail_thread.finished.connect(self._thumbnail_finished)
        self.thumbnail_thread.start()

    def _thumbnail_ready(self, photo_id: int, path: str) -> None:
        self.grid.set_thumbnail(photo_id, path)

    def _thumbnail_finished(self) -> None:
        if self.thumbnail_worker:
            self.thumbnail_worker.deleteLater()
        if self.thumbnail_thread:
            self.thumbnail_thread.deleteLater()
        self.thumbnail_worker = None
        self.thumbnail_thread = None
        if self._thumbnail_refresh_pending and not self._closing:
            self._thumbnail_refresh_pending = False
            self._load_thumbnails(getattr(self, "_visible_records", ()))

    def show_photo(self, photo_id: int) -> None:
        record = self.controller.repository.get(photo_id)
        if record:
            self.preview.show_record(record, photo_id in self.controller.rename_selection)
            self.preview.setFocus()

    def toggle_rename(self, photo_id: int, selected: bool) -> None:
        self.controller.set_selected_for_rename(photo_id, selected)
        if self.preview.record and self.preview.record.id == photo_id:
            self.preview.set_rename_selected(selected)
        self.refresh(load=False) if self.folder_panel.selected.isChecked() else self.update_chrome()

    def mark_selected_for_rename(self, photo_ids: list[int]) -> None:
        self.controller.mark_for_rename(photo_ids)
        self.refresh(load=False)

    def remove_selected_from_rename(self, photo_ids: list[int] | None = None) -> None:
        ids = photo_ids if photo_ids is not None else sorted(self.grid.selected_photo_ids())
        self.controller.remove_from_rename(ids)
        self.refresh(load=False)

    def toggle_selected_for_rename(self, photo_ids: list[int]) -> None:
        self.controller.toggle_rename_selection(photo_ids)
        self.refresh(load=False)

    def copy_selected(self, field: str) -> None:
        values = []
        for photo_id in sorted(self.grid.selected_photo_ids()):
            record = self.controller.repository.get(photo_id)
            if record:
                values.append(record.path if field == "path" else Path(record.path).name)
        if values:
            QGuiApplication.clipboard().setText("\n".join(values))

    def change_thumbnail_size(self, preset: str) -> None:
        if preset not in VALID_THUMBNAIL_SIZES:
            return
        self.saved_thumbnail_size = preset
        self.settings.setValue(THUMBNAIL_SIZE_KEY, preset)
        self.settings.sync()
        if preset in self.size_actions:
            self.size_actions[preset].setChecked(True)
        self.grid.set_thumbnail_size(preset)
        self.refresh(load=False)

    def show_context_menu(self, photo_id: int, position) -> None:
        record = self.controller.repository.get(photo_id)
        if record is None:
            return
        target_ids = self.grid.context_target_ids(photo_id)
        menu = QMenu(self)
        preview = menu.addAction("Preview")
        open_photo = menu.addAction("Open Photo")
        show_explorer = menu.addAction("Show in Explorer")
        menu.addSeparator()
        mark = menu.addAction("Mark for Rename")
        remove = menu.addAction("Remove from Rename")
        toggle = menu.addAction("Toggle Rename Selection")
        menu.addSeparator()
        copy_path = menu.addAction("Copy Full Path")
        copy_filename = menu.addAction("Copy Filename")
        show_duplicates = menu.addAction("Show Exact Duplicates")
        selected_records = [self.controller.repository.get(item_id) for item_id in target_ids]
        missing_records = [item for item in selected_records if item and (item.status == "missing" or not Path(item.path).is_file())]
        remove_missing = menu.addAction("Remove Missing Record from View") if missing_records else None
        path = Path(record.path)
        open_photo.setEnabled(path.is_file())
        show_explorer.setEnabled(path.parent.is_dir())
        show_duplicates.setEnabled(bool(record.duplicate_group))
        chosen = menu.exec(position)
        if chosen == preview:
            self.show_photo(photo_id)
        elif chosen == open_photo:
            self.preview.show_record(record, photo_id in self.controller.rename_selection)
            self.preview.open_photo()
        elif chosen == show_explorer:
            self.preview.show_record(record, photo_id in self.controller.rename_selection)
            self.preview.show_in_explorer()
        elif chosen == mark:
            self.mark_selected_for_rename(target_ids)
        elif chosen == remove:
            self.remove_selected_from_rename(target_ids)
        elif chosen == toggle:
            self.toggle_selected_for_rename(target_ids)
        elif chosen == copy_path:
            self.copy_selected("path")
        elif chosen == copy_filename:
            self.copy_selected("filename")
        elif chosen == show_duplicates:
            self.folder_panel.duplicates.setChecked(True)
            self.search.setText(record.duplicate_group or "")
            self.apply_filters()
        elif remove_missing is not None and chosen == remove_missing:
            removed = self.controller.remove_missing_records(item.id for item in missing_records if item and item.id is not None)
            self.statusBar().showMessage(f"Removed {removed} missing record(s) from the catalog view", 7000)
            self.refresh()

    def _set_filter_checkbox(self, checkbox, checked: bool) -> None:
        checkbox.setChecked(checked)
        self.apply_filters()

    def set_theme(self, theme: str, *, persist: bool = True) -> None:
        theme = theme if theme in VALID_THEMES else "System"
        self.active_theme = theme
        if persist:
            self.settings.setValue(THEME_KEY, theme)
            self.settings.sync()
        self.theme_actions[theme].setChecked(True)
        if theme == "Dark":
            self.setStyleSheet(_DARK_STYLE)
        elif theme == "Light":
            self.setStyleSheet(_LIGHT_STYLE)
        else:
            self.setStyleSheet(_SHARED_STYLE)

    def export_manifest(self) -> None:
        suggested = self.controller.paths.manifests / "rename-manifest.csv"
        target, _ = QFileDialog.getSaveFileName(self, "Export Rename Manifest", str(suggested), "CSV files (*.csv)")
        if target:
            try:
                self.controller.export_manifest(target)
            except (OSError, PermissionError) as exc:
                log.exception("Manifest export failed")
                self._error("Export failed", str(exc))
            else:
                self.statusBar().showMessage(f"Manifest exported to {target}", 10000)

    def rename_selected(self) -> None:
        review = self.controller.rename_review()
        dialog = RenameReviewDialog(review, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            results = self.controller.rename_selected()
        except (OSError, ValueError) as exc:
            log.exception("Rename failed")
            self._error("Rename could not start", str(exc))
            return
        succeeded = sum(result.renamed for result in results)
        failed = len(results) - succeeded
        QMessageBox.information(self, "Rename complete", f"Renamed: {succeeded}\nFailed or skipped: {failed}")
        self.refresh()

    def undo_latest(self) -> None:
        if QMessageBox.question(self, "Undo latest rename", "Restore filenames from the latest eligible batch?") != QMessageBox.StandardButton.Yes:
            return
        try:
            results = self.controller.undo_latest()
        except (OSError, ValueError) as exc:
            log.exception("Undo failed")
            self._error("Undo failed", str(exc))
            return
        restored = sum(result.undone for result in results)
        skipped = sum(not result.undone and result.error and "overwrite" in result.error for result in results)
        failed = len(results) - restored - skipped
        QMessageBox.information(self, "Undo complete", f"Restored: {restored}\nSkipped: {skipped}\nFailed: {failed}")
        self.refresh()

    def _error(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message or "The operation could not be completed. See the application log for details.")

    def closeEvent(self, event) -> None:
        self._closing = True
        self._thumbnail_refresh_pending = False
        self.cancel_scan()
        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.quit()
            self.scan_thread.wait(3000)
        if self.thumbnail_thread and self.thumbnail_thread.isRunning():
            if self.thumbnail_worker:
                self.thumbnail_worker.cancel()
            self.thumbnail_thread.quit()
            # ThumbnailWorker checks cancellation between files.  Wait for its
            # current file to finish so Qt never destroys a running QThread.
            self.thumbnail_thread.wait()
        super().closeEvent(event)


_SHARED_STYLE = """
QFrame#photoTile { border: 1px solid palette(mid); border-radius: 9px; background: palette(base); }
QFrame#photoTile[uiSelected="true"] { border: 3px solid #4c8dff; background: #dbe9ff; }
QLabel#tileImage { background: #20242b; border-radius: 5px; color: #b8bec8; }
QLabel#tileFilename { font-weight: 600; }
QLabel#duplicateBadge { background: #805ad5; color: white; border-radius: 4px; padding: 2px 5px; font-weight: 700; }
QLabel#missingBadge { background: #c53030; color: white; border-radius: 4px; padding: 2px 5px; font-weight: 700; }
QWidget#metadataPanel { border-top: 1px solid palette(mid); }
QLabel#metadataValue { font-weight: 500; }
QLabel#statusSummary { padding-left: 8px; font-weight: 600; }
QGroupBox#metadataSection { font-weight: 700; border: 1px solid palette(mid); border-radius: 6px; margin-top: 8px; padding-top: 7px; }
QGroupBox#metadataSection::title { subcontrol-origin: margin; left: 9px; padding: 0 4px; }
QCheckBox::indicator:checked { background: #3478d4; border: 1px solid #78a9ff; }
"""

_LIGHT_STYLE = _SHARED_STYLE + """
QMainWindow { background: #f4f6f8; }
QListWidget { background: #eef1f4; border: 0; }
"""

_DARK_STYLE = _SHARED_STYLE + """
QWidget { background: #20242b; color: #e8eaed; }
QMainWindow, QListWidget, QScrollArea { background: #171a1f; }
QFrame#photoTile { background: #282d35; border-color: #414852; }
QFrame#photoTile[uiSelected="true"] { background: #253b5d; border-color: #6ea1ff; }
QLineEdit, QComboBox, QTableWidget, QListWidget { background: #252a31; color: #e8eaed; border: 1px solid #414852; }
QPushButton { background: #303640; border: 1px solid #4a515d; border-radius: 4px; padding: 5px 9px; }
QPushButton:hover { background: #3b424e; }
QToolBar, QStatusBar, QMenuBar, QMenu { background: #252a31; border-color: #414852; }
QMenu::item:selected { background: #315b91; color: white; }
QGroupBox#metadataSection { border-color: #414852; }
"""
