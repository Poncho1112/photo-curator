"""Helpers for thumbnail-list item presentation."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QListWidgetItem
from pathlib import Path

from engine.database.models import PhotoRecord


class PhotoTile(QListWidgetItem):
    def __init__(self, record: PhotoRecord, selected_for_rename: bool = False) -> None:
        flags = []
        if selected_for_rename:
            flags.append("RENAME")
        if record.duplicate_group:
            flags.append("DUPLICATE")
        if record.status == "missing":
            flags.append("MISSING")
        suffix = f"\n[{' · '.join(flags)}]" if flags else ""
        super().__init__(f"{Path(record.path).name}{suffix}")
        self.setData(Qt.ItemDataRole.UserRole, record.id)
        self.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
        if record.status == "missing":
            self.setForeground(QColor("#b42318"))

    def set_thumbnail(self, path: str) -> None:
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self.setIcon(QIcon(pixmap))
