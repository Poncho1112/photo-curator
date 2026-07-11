"""Responsive thumbnail grid with independent UI and rename selection."""

from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QAbstractItemView, QListView, QListWidget, QListWidgetItem, QRubberBand

from app.widgets.photo_tile import PhotoTile
from engine.database.models import PhotoRecord


THUMBNAIL_SIZES = {"Small": 170, "Medium": 240, "Large": 300}
TILE_CREATION_CHUNK_SIZE = 200


class PhotoGrid(QListWidget):
    photo_activated = Signal(int)
    rename_toggled = Signal(int, bool)
    context_requested = Signal(int, object)
    mark_requested = Signal(list)
    remove_requested = Signal(list)
    toggle_requested = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.thumbnail_width = THUMBNAIL_SIZES["Medium"]
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionRectVisible(True)
        self.setDragEnabled(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.itemSelectionChanged.connect(self._sync_selection_style)
        self.itemDoubleClicked.connect(self._activate_item)
        self.customContextMenuRequested.connect(self._context_menu)
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._rubber_origin = QPoint()
        self._rubber_modifiers = Qt.KeyboardModifier.NoModifier
        self._rubber_base: set[int] = set()
        self._items_by_id: dict[int, QListWidgetItem] = {}
        self._records_by_id: dict[int, PhotoRecord] = {}
        self._pending_tile_ids: list[int] = []
        self._pending_thumbnail_paths: dict[int, str] = {}
        self._rename_selected_ids: set[int] = set()
        self._tile_timer = QTimer(self)
        self._tile_timer.setInterval(0)
        self._tile_timer.timeout.connect(self._materialize_next_chunk)
        self._apply_size()

    def set_thumbnail_size(self, preset: str) -> None:
        self.thumbnail_width = THUMBNAIL_SIZES.get(preset, THUMBNAIL_SIZES["Medium"])
        self._apply_size()

    def _apply_size(self) -> None:
        width = self.thumbnail_width + 24
        self.setIconSize(QSize(self.thumbnail_width, round(self.thumbnail_width * 0.72)))
        self.setGridSize(QSize(width, round(self.thumbnail_width * 0.72) + 82))

    def selected_photo_ids(self) -> set[int]:
        return {int(item.data(Qt.ItemDataRole.UserRole)) for item in self.selectedItems()}

    def context_target_ids(self, clicked_photo_id: int) -> list[int]:
        item = self.item_for_id(clicked_photo_id)
        if item is None:
            return []
        if not item.isSelected():
            self.clearSelection()
            item.setSelected(True)
            self.setCurrentItem(item, QItemSelectionModel.SelectionFlag.NoUpdate)
        return sorted(self.selected_photo_ids())

    def populate(self, records: list[PhotoRecord], selected_ids: set[int]) -> None:
        previous_ui_selection = self.selected_photo_ids()
        self.clear()
        self._rename_selected_ids = set(selected_ids)
        for record in records:
            if record.id is None:
                continue
            photo_id = int(record.id)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, photo_id)
            item.setSizeHint(self.gridSize())
            self.addItem(item)
            self._items_by_id[photo_id] = item
            self._records_by_id[photo_id] = record
            self._pending_tile_ids.append(photo_id)
            if photo_id in previous_ui_selection:
                item.setSelected(True)
        self._materialize_next_chunk()
        if self._pending_tile_ids:
            self._tile_timer.start()
        self._sync_selection_style()

    def clear(self) -> None:
        """Clear items and cancel tile work belonging to the old population."""
        if hasattr(self, "_tile_timer"):
            self._tile_timer.stop()
            self._items_by_id.clear()
            self._records_by_id.clear()
            self._pending_tile_ids.clear()
            self._pending_thumbnail_paths.clear()
            self._rename_selected_ids.clear()
        super().clear()

    def _materialize_next_chunk(self) -> None:
        chunk = self._pending_tile_ids[:TILE_CREATION_CHUNK_SIZE]
        del self._pending_tile_ids[: len(chunk)]
        for photo_id in chunk:
            item = self._items_by_id.get(photo_id)
            record = self._records_by_id.get(photo_id)
            if item is None or record is None:
                continue
            tile = PhotoTile(record, photo_id in self._rename_selected_ids, self.thumbnail_width)
            tile.rename_toggled.connect(self.rename_toggled)
            tile.clicked.connect(self._tile_clicked)
            tile.activated.connect(self.photo_activated)
            tile.context_requested.connect(self._tile_context)
            tile.drag_started.connect(self._start_tile_rubber_band)
            tile.drag_moved.connect(self._move_tile_rubber_band)
            tile.drag_finished.connect(self._finish_tile_rubber_band)
            self.setItemWidget(item, tile)
            tile.set_ui_selected(item.isSelected())
            thumbnail_path = self._pending_thumbnail_paths.pop(photo_id, None)
            if thumbnail_path is not None:
                tile.set_thumbnail(thumbnail_path)
        if not self._pending_tile_ids:
            self._tile_timer.stop()

    def set_thumbnail(self, photo_id: int, path: str) -> None:
        item = self.item_for_id(photo_id)
        tile = self.itemWidget(item) if item else None
        if isinstance(tile, PhotoTile):
            tile.set_thumbnail(path)
        elif item is not None:
            self._pending_thumbnail_paths[photo_id] = path

    def item_for_id(self, photo_id: int) -> QListWidgetItem | None:
        return self._items_by_id.get(photo_id)

    def _sync_selection_style(self) -> None:
        for index in range(self.count()):
            item = self.item(index)
            tile = self.itemWidget(item)
            if isinstance(tile, PhotoTile):
                tile.set_ui_selected(item.isSelected())

    def _activate_item(self, item: QListWidgetItem) -> None:
        self.photo_activated.emit(int(item.data(Qt.ItemDataRole.UserRole)))

    def _tile_clicked(self, photo_id: int, modifiers) -> None:
        item = self.item_for_id(photo_id)
        if item is None:
            return
        if modifiers & Qt.KeyboardModifier.ShiftModifier and self.currentItem():
            start, end = sorted((self.row(self.currentItem()), self.row(item)))
            if not modifiers & Qt.KeyboardModifier.ControlModifier:
                self.clearSelection()
            for row in range(start, end + 1):
                self.item(row).setSelected(True)
        elif modifiers & Qt.KeyboardModifier.ControlModifier:
            item.setSelected(not item.isSelected())
        else:
            self.clearSelection()
            item.setSelected(True)
        self.setCurrentItem(item, QItemSelectionModel.SelectionFlag.NoUpdate)

    def _tile_context(self, photo_id: int, position) -> None:
        self.context_target_ids(photo_id)
        self.context_requested.emit(photo_id, position)

    def _start_tile_rubber_band(self, global_position, modifiers) -> None:
        self._rubber_origin = self.viewport().mapFromGlobal(global_position)
        self._rubber_modifiers = modifiers
        self._rubber_base = self.selected_photo_ids() if modifiers & Qt.KeyboardModifier.ControlModifier else set()

    def _move_tile_rubber_band(self, global_position) -> None:
        current = self.viewport().mapFromGlobal(global_position)
        rectangle = QRect(self._rubber_origin, current).normalized()
        if rectangle.width() + rectangle.height() < 8:
            return
        self._rubber_band.setGeometry(rectangle)
        self._rubber_band.show()
        for index in range(self.count()):
            item = self.item(index)
            photo_id = int(item.data(Qt.ItemDataRole.UserRole))
            selected = self.visualItemRect(item).intersects(rectangle) or photo_id in self._rubber_base
            item.setSelected(selected)

    def _finish_tile_rubber_band(self) -> None:
        self._rubber_band.hide()

    def _context_menu(self, position) -> None:
        item = self.itemAt(position)
        if item:
            photo_id = int(item.data(Qt.ItemDataRole.UserRole))
            self.context_target_ids(photo_id)
            self.context_requested.emit(photo_id, self.viewport().mapToGlobal(position))

    def toggle_rename_for_ui_selection(self) -> None:
        photo_ids = sorted(self.selected_photo_ids())
        if photo_ids:
            self.toggle_requested.emit(photo_ids)

    def select_all_visible(self) -> None:
        self.selectAll()

    def clear_ui_selection(self) -> None:
        self.clearSelection()
        self.setCurrentItem(None)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.toggle_rename_for_ui_selection()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Delete:
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentItem():
            self._activate_item(self.currentItem())
            event.accept()
            return
        super().keyPressEvent(event)
