"""Read-only metadata display."""

from PySide6.QtWidgets import QFormLayout, QLabel, QWidget


class MetadataPanel(QWidget):
    FIELDS = ("Filename", "Path", "Proposed", "Capture date", "Date source", "Camera", "Dimensions", "File size", "SHA-256", "Duplicate group", "Status")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QFormLayout(self)
        self.labels: dict[str, QLabel] = {}
        for field in self.FIELDS:
            label = QLabel("—")
            label.setWordWrap(True)
            label.setTextInteractionFlags(label.textInteractionFlags())
            layout.addRow(f"{field}:", label)
            self.labels[field] = label

    def set_values(self, values: dict[str, str]) -> None:
        for field, label in self.labels.items():
            label.setText(values.get(field) or "—")

    def clear(self) -> None:
        self.set_values({})

