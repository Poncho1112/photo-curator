"""Folder and filter controls."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget


class FolderPanel(QWidget):
    filters_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.folders = QListWidget()
        self.add_button = QPushButton("Add Folder")
        self.remove_button = QPushButton("Remove Folder")
        self.duplicates = QCheckBox("Exact duplicates only")
        self.missing = QCheckBox("Missing files only")
        self.renamed = QCheckBox("Renamed files only")
        self.selected = QCheckBox("Selected for rename only")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Folders"))
        layout.addWidget(self.folders, 1)
        layout.addWidget(self.add_button)
        layout.addWidget(self.remove_button)
        layout.addSpacing(12)
        layout.addWidget(QLabel("Filters"))
        for checkbox in (self.duplicates, self.missing, self.renamed, self.selected):
            layout.addWidget(checkbox)
            checkbox.toggled.connect(self.filters_changed)

    def folder_paths(self) -> list[str]:
        return [self.folders.item(index).text() for index in range(self.folders.count())]

