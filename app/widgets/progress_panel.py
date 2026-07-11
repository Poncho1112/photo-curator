"""Compact scan progress widget."""

from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget


class ProgressPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.label = QLabel("Ready")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        layout.addWidget(self.bar)

    def update_progress(self, current: int, total: int, path: str) -> None:
        self.bar.setRange(0, max(total, 1))
        self.bar.setValue(current)
        self.label.setText(f"{current} of {total}: {path}")

