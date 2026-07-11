"""Rich thumbnail tile with explicit rename selection."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout

from engine.database.models import PhotoRecord


class PhotoTile(QFrame):
    rename_toggled = Signal(int, bool)
    clicked = Signal(int, object)
    activated = Signal(int)
    context_requested = Signal(int, object)
    drag_started = Signal(object, object)
    drag_moved = Signal(object)
    drag_finished = Signal()

    def __init__(self, record: PhotoRecord, selected_for_rename: bool = False, thumbnail_width: int = 240) -> None:
        super().__init__()
        self.photo_id = int(record.id)
        self.thumbnail_width = thumbnail_width
        self.setObjectName("photoTile")
        self.setProperty("uiSelected", False)
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self.image = QLabel("No preview")
        self.image.setObjectName("tileImage")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setFixedSize(thumbnail_width, round(thumbnail_width * 0.72))

        self.filename = QLabel(_short_name(Path(record.path).name))
        self.filename.setObjectName("tileFilename")
        self.filename.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.filename.setToolTip(record.path)

        self.rename_checkbox = QCheckBox("Rename")
        self.rename_checkbox.setChecked(selected_for_rename)
        self.rename_checkbox.setToolTip("Include this photo in the next reviewed rename batch")

        badges = QHBoxLayout()
        badges.setContentsMargins(0, 0, 0, 0)
        badges.addWidget(self.rename_checkbox)
        badges.addStretch()
        if record.duplicate_group:
            duplicate = QLabel("DUP")
            duplicate.setObjectName("duplicateBadge")
            duplicate.setToolTip(f"Exact duplicate group {record.duplicate_group}")
            badges.addWidget(duplicate)
        if record.status == "missing" or not Path(record.path).is_file():
            missing = QLabel("MISSING")
            missing.setObjectName("missingBadge")
            missing.setToolTip("The indexed file is no longer available")
            badges.addWidget(missing)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(badges)
        layout.addWidget(self.image, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.filename)
        self.rename_checkbox.toggled.connect(lambda checked: self.rename_toggled.emit(self.photo_id, checked))

    def set_thumbnail(self, path: str) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.image.setText("Preview unavailable")
            return
        self.image.setPixmap(
            pixmap.scaled(
                self.image.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )
        self.image.setText("")

    def set_ui_selected(self, selected: bool) -> None:
        self.setProperty("uiSelected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.photo_id, event.modifiers())
            self.drag_started.emit(event.globalPosition().toPoint(), event.modifiers())
        elif event.button() == Qt.MouseButton.RightButton:
            self.context_requested.emit(self.photo_id, event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.drag_moved.emit(event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_finished.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.photo_id)
        super().mouseDoubleClickEvent(event)


def _short_name(name: str, limit: int = 32) -> str:
    return name if len(name) <= limit else f"{name[:14]}…{name[-14:]}"
