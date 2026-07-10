"""Safe rename review dialog."""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout

from app.controllers.library_controller import RenameReviewItem


class RenameReviewDialog(QDialog):
    def __init__(self, items: list[RenameReviewItem], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review Rename Operation")
        self.resize(900, 420)
        table = QTableWidget(len(items), 5)
        table.setHorizontalHeaderLabels(("Current path", "Proposed filename", "Final destination", "Conflict", "Missing"))
        for row, item in enumerate(items):
            values = (str(item.current_path), item.proposed_path.name, str(item.proposed_path), "Yes" if item.conflict else "No", "Yes" if item.missing else "No")
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        table.resizeColumnsToContents()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Rename Safely")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(items) and all(item.safe for item in items))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{len(items)} selected photo(s). Existing files will never be overwritten."))
        layout.addWidget(table)
        layout.addWidget(buttons)

