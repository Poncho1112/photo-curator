"""Photo Curator main window."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt
from PySide6.QtWidgets import QFileDialog, QLineEdit, QMainWindow, QMessageBox, QSplitter, QToolBar

from app.controllers.library_controller import LibraryController
from app.views.folder_panel import FolderPanel
from app.views.photo_grid import PhotoGrid
from app.views.photo_preview import PhotoPreview
from app.views.rename_review import RenameReviewDialog
from app.widgets.progress_panel import ProgressPanel
from app.workers.scan_worker import ScanWorker
from app.workers.thumbnail_worker import ThumbnailWorker


log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, controller: LibraryController) -> None:
        super().__init__()
        self.controller = controller
        self.scan_thread: QThread | None = None
        self.scan_worker = None
        self.thumbnail_thread: QThread | None = None
        self.thumbnail_worker = None
        self.scan_errors: list[tuple[str, str]] = []
        self.setWindowTitle("Photo Curator")
        self.resize(1450, 850)
        self._build_ui()
        self._connect()
        self.refresh()

    def _build_ui(self) -> None:
        toolbar = QToolBar("Library")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.add_folder_action = toolbar.addAction("Add Folder")
        self.remove_folder_action = toolbar.addAction("Remove Folder")
        self.scan_action = toolbar.addAction("Scan")
        self.cancel_action = toolbar.addAction("Cancel Scan")
        self.cancel_action.setEnabled(False)
        toolbar.addSeparator()
        self.export_action = toolbar.addAction("Export Manifest")
        self.rename_action = toolbar.addAction("Rename Selected")
        self.undo_action = toolbar.addAction("Undo Last Batch")
        toolbar.addSeparator()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search filename, path, date, camera, status…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(300)
        toolbar.addWidget(self.search)

        self.folder_panel = FolderPanel()
        self.grid = PhotoGrid()
        self.preview = PhotoPreview()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.folder_panel)
        splitter.addWidget(self.grid)
        splitter.addWidget(self.preview)
        splitter.setSizes([240, 760, 450])
        self.setCentralWidget(splitter)
        self.progress = ProgressPanel()
        self.statusBar().addPermanentWidget(self.progress, 1)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(250)

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

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add Photo Folder")
        if folder and folder not in self.folder_panel.folder_paths():
            self.folder_panel.folders.addItem(folder)

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
        self.grid.populate(records, self.controller.rename_selection)
        self.statusBar().showMessage(f"{len(records)} photo(s); {len(self.controller.rename_selection)} selected for rename")
        self._load_thumbnails(records)

    def _load_thumbnails(self, records) -> None:
        if ThumbnailWorker is None or self.thumbnail_thread is not None:
            return
        requests = []
        for record in records:
            if record.id is not None and Path(record.path).is_file():
                target = self.controller.paths.thumbnails / f"{record.sha256}.jpg"
                requests.append((record.id, record.path, str(target)))
        if not requests:
            return
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
        for index in range(self.grid.count()):
            item = self.grid.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == photo_id:
                item.set_thumbnail(path)
                break

    def _thumbnail_finished(self) -> None:
        if self.thumbnail_worker:
            self.thumbnail_worker.deleteLater()
        if self.thumbnail_thread:
            self.thumbnail_thread.deleteLater()
        self.thumbnail_worker = None
        self.thumbnail_thread = None

    def show_photo(self, photo_id: int) -> None:
        record = self.controller.repository.get(photo_id)
        if record:
            self.preview.show_record(record)

    def toggle_rename(self, photo_id: int, selected: bool) -> None:
        self.controller.set_selected_for_rename(photo_id, selected)
        self.refresh(load=False)

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
        self.cancel_scan()
        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.quit()
            self.scan_thread.wait(3000)
        if self.thumbnail_thread and self.thumbnail_thread.isRunning():
            self.thumbnail_thread.quit()
            self.thumbnail_thread.wait(3000)
        super().closeEvent(event)
