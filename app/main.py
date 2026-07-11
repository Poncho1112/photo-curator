"""Application bootstrap."""

from __future__ import annotations

import logging
import sys

from app.controllers.library_controller import LibraryController
from app.paths import AppPaths
from engine.database.repository import PhotoRepository


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError:
        print("Photo Curator requires PySide6 and Pillow. Run: python -m pip install -e .", file=sys.stderr)
        return 1

    paths = AppPaths.default()
    logging.basicConfig(filename=paths.log, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = QApplication(sys.argv)
    app.setApplicationName("Photo Curator")
    repository = None
    try:
        repository = PhotoRepository(paths.database)
        controller = LibraryController(repository, paths)
        from app.ui.main_window import MainWindow
        window = MainWindow(controller)
        window.show()
        return app.exec()
    except Exception as exc:
        logging.exception("Photo Curator failed to start")
        QMessageBox.critical(None, "Photo Curator", f"The application could not start: {exc}\n\nSee {paths.log} for technical details.")
        return 1
    finally:
        if repository is not None:
            repository.close()

