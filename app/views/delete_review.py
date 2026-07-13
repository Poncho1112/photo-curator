"""Review exact duplicate files before moving copies to the Recycle Bin."""

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.controllers.library_controller import DeleteReviewItem


class DeleteReviewDialog(QDialog):
    def __init__(
        self,
        items: list[DeleteReviewItem],
        total_reclaimable_bytes: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review Duplicate Deletion")
        self.resize(950, 480)

        delete_count = sum(len(item.to_delete) for item in items)
        total_files = sum(1 + len(item.to_delete) for item in items)
        reclaimable_mb = total_reclaimable_bytes / (1024 * 1024)

        self.table = QTableWidget(total_files, 4)
        self.table.setHorizontalHeaderLabels(("Group", "Action", "Path", "Size"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        row = 0
        for item in items:
            row = self._add_record_row(row, item.group_key, "KEEP", item.survivor)
            for record in item.to_delete:
                row = self._add_record_row(row, item.group_key, "DELETE", record)
        self.table.resizeColumnsToContents()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(f"Move {delete_count} copies to Recycle Bin")
        self.ok_button.setEnabled(delete_count > 0)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                f"{delete_count} duplicate copies will be moved; "
                f"{reclaimable_mb:.1f} MB can be reclaimed."
            )
        )
        layout.addWidget(
            QLabel(
                "Files are moved to the Recycle Bin, not permanently deleted, "
                "and can be restored with Undo Delete."
            )
        )
        layout.addWidget(self.table)
        layout.addWidget(self.buttons)

    def _add_record_row(self, row: int, group: str, action: str, record) -> int:
        values = (group, action, str(record.path), self._format_size(record.size))
        for column, value in enumerate(values):
            self.table.setItem(row, column, QTableWidgetItem(value))
        return row + 1

    @staticmethod
    def _format_size(size: int) -> str:
        return f"{size / (1024 * 1024):.1f} MB"
