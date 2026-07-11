"""Folder and filter controls."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from engine.database.models import PhotoRecord


class FolderPanel(QWidget):
    filters_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.folders = QListWidget()
        self.folders.setMaximumHeight(210)
        self.add_button = QPushButton("Add Folder")
        self.remove_button = QPushButton("Remove Folder")
        self.duplicates = QCheckBox("Exact duplicates only")
        self.missing = QCheckBox("Missing files only")
        self.renamed = QCheckBox("Renamed files only")
        self.selected = QCheckBox("Selected for rename only")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Folders"))
        layout.addWidget(self.folders)
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        layout.addLayout(buttons)
        layout.addSpacing(12)
        layout.addWidget(QLabel("Filters"))
        for checkbox in (self.duplicates, self.missing, self.renamed, self.selected):
            layout.addWidget(checkbox)
            checkbox.toggled.connect(self.filters_changed)
        layout.addStretch(1)

    def folder_paths(self) -> list[str]:
        return [str(self.folders.item(index).data(Qt.ItemDataRole.UserRole)) for index in range(self.folders.count())]

    def add_folder(self, path: str) -> None:
        item = QListWidgetItem(path)
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        self.folders.addItem(item)

    def refresh_counts(self, records: list[PhotoRecord]) -> None:
        roots = [
            _normalized_path(str(self.folders.item(index).data(Qt.ItemDataRole.UserRole)))
            for index in range(self.folders.count())
        ]
        counts = _aggregate_folder_counts(records, roots)
        for index, root in enumerate(roots):
            item = self.folders.item(index)
            photo_count, missing = counts[index]
            suffix = f"  —  {photo_count} photos"
            if missing:
                suffix += f", {missing} missing"
            item.setText(f"{root.name or root}{suffix}")


def _beneath(path: str, root: Path) -> bool:
    return _normalized_beneath(_normalized_path(path), _normalized_path(root))


def _normalized_path(path: str | Path) -> Path:
    try:
        return Path(path).resolve(strict=False)
    except OSError:
        return Path(path).absolute()


def _normalized_beneath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _aggregate_folder_counts(records: list[PhotoRecord], roots: list[Path]) -> list[tuple[int, int]]:
    """Aggregate folder totals while normalizing and statting each record once."""

    totals = [[0, 0] for _ in roots]
    for record in records:
        path = _normalized_path(record.path)
        is_missing = record.status == "missing" or not path.is_file()
        for index, root in enumerate(roots):
            if _normalized_beneath(path, root):
                totals[index][0] += 1
                totals[index][1] += int(is_missing)
    return [(photo_count, missing_count) for photo_count, missing_count in totals]
