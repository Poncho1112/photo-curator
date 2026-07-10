"""Keyboard-accessible thumbnail grid."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QListView, QListWidget

from app.widgets.photo_tile import PhotoTile
from engine.database.models import PhotoRecord


class PhotoGrid(QListWidget):
    photo_activated = Signal(int)
    rename_toggled = Signal(int, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setIconSize(QSize(160, 120))
        self.setGridSize(QSize(190, 175))
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.itemSelectionChanged.connect(self._emit_selection)
        self.itemDoubleClicked.connect(self._toggle_item)

    def populate(self, records: list[PhotoRecord], selected_ids: set[int]) -> None:
        self.clear()
        for record in records:
            self.addItem(PhotoTile(record, record.id in selected_ids))

    def _emit_selection(self) -> None:
        items = self.selectedItems()
        if items:
            self.photo_activated.emit(int(items[0].data(Qt.ItemDataRole.UserRole)))

    def _toggle_item(self, item) -> None:
        photo_id = int(item.data(Qt.ItemDataRole.UserRole))
        self.rename_toggled.emit(photo_id, "RENAME" not in item.text())

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and self.currentItem():
            item = self.currentItem()
            photo_id = int(item.data(Qt.ItemDataRole.UserRole))
            self.rename_toggled.emit(photo_id, "RENAME" not in item.text())
            return
        super().keyPressEvent(event)
