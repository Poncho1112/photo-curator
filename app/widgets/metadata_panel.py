"""Sectioned, selectable photo metadata display."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel, QVBoxLayout, QWidget


class MetadataPanel(QWidget):
    SECTIONS = {
        "File": ("Filename", "Path", "Folder", "File size", "Dimensions"),
        "Capture": ("Capture date", "Date source", "Camera"),
        "Organization": ("Proposed", "Status", "Duplicate group", "SHA-256"),
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("metadataPanel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(8)
        self.labels: dict[str, QLabel] = {}
        for section, fields in self.SECTIONS.items():
            group = QGroupBox(section)
            group.setObjectName("metadataSection")
            form = QFormLayout(group)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setHorizontalSpacing(14)
            form.setVerticalSpacing(5)
            for field in fields:
                label = QLabel("—")
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                label.setObjectName("metadataValue")
                form.addRow(f"{field}:", label)
                self.labels[field] = label
            outer.addWidget(group)

    def set_values(self, values: dict[str, str]) -> None:
        for field, label in self.labels.items():
            label.setText(values.get(field) or "—")

    def clear(self) -> None:
        self.set_values({})

