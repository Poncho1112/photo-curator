"""Scaled preview and photo metadata pane."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from app.widgets.metadata_panel import MetadataPanel
from engine.database.models import PhotoRecord


class PhotoPreview(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.record: PhotoRecord | None = None
        self.image = QLabel("Select a photo")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(260)
        self.image.setScaledContents(False)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.image)
        self.metadata = MetadataPanel()
        self.open_button = QPushButton("Open Photo")
        self.explorer_button = QPushButton("Show in Explorer")
        buttons = QHBoxLayout()
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.explorer_button)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.metadata)
        layout.addLayout(buttons)
        self.open_button.clicked.connect(self.open_photo)
        self.explorer_button.clicked.connect(self.show_in_explorer)

    def show_record(self, record: PhotoRecord) -> None:
        self.record = record
        path = Path(record.path)
        if path.is_file():
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            pixmap = QPixmap.fromImage(reader.read())
            self.image.setPixmap(pixmap.scaled(520, 420, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)) if not pixmap.isNull() else self.image.setText("Unsupported preview")
        else:
            self.image.setPixmap(QPixmap())
            self.image.setText("File is missing")
        camera = " ".join(filter(None, (record.camera_make, record.camera_model)))
        self.metadata.set_values({
            "Filename": path.name, "Path": record.path, "Proposed": record.proposed_name or path.name,
            "Capture date": record.captured_at or "", "Date source": record.date_source or "", "Camera": camera,
            "Dimensions": f"{record.width} × {record.height}" if record.width and record.height else "",
            "File size": _format_size(record.size), "SHA-256": record.sha256[:12],
            "Duplicate group": record.duplicate_group or "", "Status": record.status,
        })
        self.open_button.setEnabled(path.is_file())
        self.explorer_button.setEnabled(path.parent.is_dir())

    def open_photo(self) -> None:
        if self.record and Path(self.record.path).is_file():
            _open_path(Path(self.record.path))

    def show_in_explorer(self) -> None:
        if not self.record:
            return
        path = Path(self.record.path)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            _open_path(path.parent)


def _open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
