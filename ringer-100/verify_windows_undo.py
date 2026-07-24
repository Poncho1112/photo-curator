"""Host verifier for Photo Curator's Windows Delete Duplicates / Undo Delete flow.

Run ONLY on Windows, by a human or the ``ringer-100`` host-verification step
of ``run_loop.py`` -- never by a Ringer worker, and never against a real
photo folder. It:

1. Guards its own inputs (Windows only; isolated root only; never a path
   that looks like a personal media folder) BEFORE importing Pillow/PySide6
   or touching the filesystem.
2. Generates two byte-identical duplicate JPEGs and one unique JPEG under an
   isolated per-round directory it creates itself.
3. Launches the real ``PySide6`` ``MainWindow`` against isolated app data.
4. Drives the connected Scan, Delete Duplicates, and Undo Delete QActions
   through their real dialogs with ``QTest``/``QTimer`` -- never calling
   ``DeleteService``/``UndoDeleteService`` directly.
5. Observes a real ``$Recycle.Bin`` destination before undo and verifies
   byte-for-byte restoration (SHA-256) and catalog status after undo.
6. Writes structured ``evidence.json`` and three screenshots.

The guard functions (``require_windows``, ``require_isolated_root``,
``require_generated_fixture``) are pure and imported/unit-tested without
importing Pillow/PySide6 or touching the real Recycle Bin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Any of these substrings appearing (case-insensitive) in a resolved path is
# treated as a signal that the path may be a real, personal media location
# rather than a disposable fixture directory this script created.
_PERSONAL_PATH_FRAGMENTS = (
    "pictures",
    "photos",
    "camera roll",
    "dcim",
    "desktop",
    "documents",
    "onedrive",
    "downloads",
)


class GuardError(RuntimeError):
    """Raised when a safety guard rejects an input; never caught silently."""


def require_windows(platform: str | None = None) -> None:
    platform = sys.platform if platform is None else platform
    if platform != "win32":
        raise GuardError(f"verify_windows_undo.py requires Windows (win32); got platform={platform!r}")


def require_isolated_root(root: Path, *, allowed_base: Path) -> Path:
    """Reject any round directory that is not safely confined under the configured state root.

    ``allowed_base`` is the state root boundary itself (not a repo root to derive it from) so
    that the boundary can stay pinned to the main checkout's state directory even when code is
    imported from a separate integration worktree (see ``run``)."""
    resolved = Path(root).resolve()
    allowed_base = Path(allowed_base).resolve()
    try:
        resolved.relative_to(allowed_base)
    except ValueError:
        raise GuardError(
            f"isolated round directory must be under {allowed_base}, got {resolved}"
        ) from None

    lowered = resolved.as_posix().lower()
    for fragment in _PERSONAL_PATH_FRAGMENTS:
        if fragment in lowered:
            raise GuardError(
                f"isolated round directory looks like a personal media location "
                f"(matched {fragment!r}): {resolved}"
            )
    return resolved


def require_generated_fixture(path: Path, *, isolated_root: Path) -> Path:
    """Reject any file operation targeting a path outside the isolated root."""
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(isolated_root)
    except ValueError:
        raise GuardError(f"refusing to touch a path outside the isolated artifact root: {resolved}") from None
    return resolved


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--round-dir",
        required=True,
        type=Path,
        help="isolated per-round directory under the configured state root's round-N/windows-verify",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="repository to import app/engine code from -- the integration worktree when invoked "
        "by run_loop.py, so the verifier exercises the fixes actually made this round",
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        default=None,
        help="state root boundary --round-dir must be confined under (default: <repo-root>/ringer-100/state, "
        "for standalone/manual use only -- run_loop.py always passes the main checkout's state root explicitly)",
    )
    return parser


def run(round_dir: Path, *, repo_root: Path = REPO_ROOT, state_root: Path | None = None) -> dict:
    """Guard inputs, then run the full UI-driven verification. Returns the evidence dict.

    ``repo_root`` is where app/engine code is imported from (the integration worktree, in the
    run_loop.py flow). ``state_root`` is the isolation boundary ``round_dir`` must be confined
    under; it defaults to ``<repo_root>/ringer-100/state`` only for standalone/manual invocation --
    run_loop.py always passes the main checkout's state root explicitly so the boundary never
    drifts into a worktree that could itself be discarded."""
    require_windows()
    allowed_base = state_root if state_root is not None else (repo_root / "ringer-100" / "state")
    isolated_root = require_isolated_root(round_dir, allowed_base=allowed_base)
    isolated_root.mkdir(parents=True, exist_ok=True)

    copied_photos = require_generated_fixture(isolated_root / "copied-photos", isolated_root=isolated_root)
    isolated_app_data = require_generated_fixture(isolated_root / "isolated-app-data", isolated_root=isolated_root)
    copied_photos.mkdir(parents=True, exist_ok=True)
    isolated_app_data.mkdir(parents=True, exist_ok=True)

    evidence: dict[str, object] = {
        "status": "started",
        "platform": sys.platform,
        "round_dir": str(isolated_root),
        "window_visible": False,
        "scan_completed": False,
        "duplicates_found": False,
        "delete_action_triggered": False,
        "delete_review_accepted": False,
        "source_missing_after_delete": False,
        "recycle_item_observed_before_undo": False,
        "trash_backend": None,
        "recycle_path": None,
        "undo_action_triggered": False,
        "undo_confirmation_accepted": False,
        "source_restored_after_undo": False,
        "restored_hash_matches": False,
        "catalog_status_restored": False,
        "restored_path": None,
        "expected_sha256": None,
        "diagnostics": {"steps": [], "errors": []},
    }

    def log(message: str) -> None:
        print(f"[verify_windows_undo] {message}", flush=True)
        evidence["diagnostics"]["steps"].append(message)

    def write_evidence() -> None:
        (isolated_root / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    def fail(message: str) -> dict:
        log(f"FAIL: {message}")
        evidence["diagnostics"]["errors"].append(message)
        evidence["status"] = "failed"
        write_evidence()
        return evidence

    sys.path.insert(0, str(repo_root))

    try:
        from PIL import Image
    except ImportError as exc:
        return fail(f"Pillow is not importable: {exc}")

    try:
        from PySide6.QtCore import QEventLoop, Qt, QTimer
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError as exc:
        return fail(f"PySide6 is not importable: {exc}")

    from app.controllers.library_controller import LibraryController
    from app.paths import AppPaths
    from app.ui.main_window import MainWindow, create_application_settings
    from app.views.delete_review import DeleteReviewDialog
    from engine.database.repository import PhotoRepository

    duplicate_a = require_generated_fixture(copied_photos / "duplicate-a.jpg", isolated_root=isolated_root)
    duplicate_b = require_generated_fixture(copied_photos / "duplicate-b.jpg", isolated_root=isolated_root)
    unique = require_generated_fixture(copied_photos / "unique-photo.jpg", isolated_root=isolated_root)

    Image.new("RGB", (64, 64), color=(200, 60, 60)).save(duplicate_a, "JPEG")
    duplicate_b.write_bytes(duplicate_a.read_bytes())
    Image.new("RGB", (48, 96), color=(30, 140, 210)).save(unique, "JPEG")

    fixture_hashes = {
        "duplicate_a": sha256_of(duplicate_a),
        "duplicate_b": sha256_of(duplicate_b),
        "unique": sha256_of(unique),
    }
    evidence["diagnostics"]["fixture_hashes"] = fixture_hashes
    if fixture_hashes["duplicate_a"] != fixture_hashes["duplicate_b"]:
        return fail("generated duplicate fixtures do not share a SHA-256; fixture generation is broken")
    if fixture_hashes["duplicate_a"] == fixture_hashes["unique"]:
        return fail("generated unique fixture collides with the duplicate fixtures; fixture generation is broken")
    log(f"Generated 2 duplicate JPEGs and 1 unique JPEG under {copied_photos}")

    paths = AppPaths.from_root(isolated_app_data)
    repository = PhotoRepository(paths.database)
    controller = LibraryController(repository, paths)
    settings = create_application_settings(paths.root / "settings.ini")

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(controller, settings=settings)
    window.show()
    window.raise_()
    window.activateWindow()
    exposed = QTest.qWaitForWindowExposed(window, 5000)
    for _ in range(5):
        app.processEvents()
    evidence["window_visible"] = bool(exposed and window.isVisible())
    log(f"MainWindow constructed and shown; window_visible={evidence['window_visible']}")
    if not evidence["window_visible"]:
        return fail("MainWindow never became visible/exposed")

    window.folder_panel.add_folder(str(copied_photos.resolve()))
    log(f"Added folder to FolderPanel: {copied_photos.resolve()}")

    scan_result: dict[str, object] = {"records": None}
    scan_loop = QEventLoop()

    def on_scan_finished(records, cancelled) -> None:
        scan_result["records"] = records
        scan_loop.quit()

    window.scan_action.trigger()
    if window.scan_worker is None:
        return fail("Scan QAction did not start a ScanWorker (no folder registered?)")
    window.scan_worker.finished.connect(on_scan_finished)
    QTimer.singleShot(30000, scan_loop.quit)
    scan_loop.exec()

    if scan_result["records"] is None:
        return fail("production scan worker did not finish within timeout")
    evidence["scan_completed"] = True
    log(f"Scan worker finished: indexed {len(scan_result['records'])} record(s)")

    duplicate_groups = controller.duplicate_groups()
    evidence["duplicates_found"] = bool(duplicate_groups)
    if not duplicate_groups:
        return fail("scan did not detect the generated duplicate JPEGs as a duplicate group")

    watcher_state: dict[str, list[str]] = {"errors": []}

    def click_when_modal(match_fn, on_shown, *, timeout_s: float = 20.0, interval_ms: int = 40) -> None:
        deadline = time.monotonic() + timeout_s

        def poll() -> None:
            widget = QApplication.activeModalWidget()
            if widget is not None and match_fn(widget):
                on_shown(widget)
                return
            if time.monotonic() >= deadline:
                watcher_state["errors"].append(f"timed out waiting for modal: {match_fn}")
                return
            QTimer.singleShot(interval_ms, poll)

        QTimer.singleShot(0, poll)

    def handle_delete_review(dialog) -> None:
        pixmap = dialog.grab()
        pixmap.save(str(isolated_root / "delete-review.png"))
        log(f"DeleteReviewDialog visible with {dialog.table.rowCount()} row(s); captured delete-review.png")
        QTest.mouseClick(dialog.ok_button, Qt.MouseButton.LeftButton)
        evidence["delete_review_accepted"] = True
        click_when_modal(
            lambda widget: isinstance(widget, QMessageBox)
            and widget.windowTitle() == "Duplicate deletion complete",
            handle_delete_complete,
        )

    def handle_delete_complete(widget) -> None:
        ok_button = widget.button(QMessageBox.StandardButton.Ok)
        QTest.mouseClick(ok_button, Qt.MouseButton.LeftButton)

    click_when_modal(lambda widget: isinstance(widget, DeleteReviewDialog), handle_delete_review)
    window.delete_duplicates_action.trigger()
    evidence["delete_action_triggered"] = True
    for _ in range(5):
        app.processEvents()

    if watcher_state["errors"]:
        return fail("delete flow dialog automation timed out: " + "; ".join(watcher_state["errors"]))
    if not evidence["delete_review_accepted"]:
        return fail("DeleteReviewDialog was never observed/accepted")

    window.grab().save(str(isolated_root / "after-delete.png"))

    if controller.last_delete_log is None or not controller.last_delete_log.is_file():
        return fail("no production deletion log was written by DeleteService")
    log_entries = [
        json.loads(line)
        for line in controller.last_delete_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not log_entries:
        return fail("production deletion log is empty; no delete was recorded")
    entry = log_entries[-1]
    deleted_original = Path(str(entry["original_path"]))
    expected_sha256 = str(entry["sha256"])
    recycle_path = str(entry.get("trashed_to") or "")
    trash_backend = str(entry.get("trash_backend") or "")

    evidence["expected_sha256"] = expected_sha256
    evidence["trash_backend"] = trash_backend
    evidence["recycle_path"] = recycle_path
    evidence["restored_path"] = str(deleted_original)

    evidence["source_missing_after_delete"] = not deleted_original.is_file()
    evidence["recycle_item_observed_before_undo"] = bool(recycle_path) and Path(recycle_path).is_file()
    if not evidence["source_missing_after_delete"]:
        return fail("original copy still exists at its source path after delete")
    if "$Recycle.Bin" not in recycle_path:
        return fail(f"recorded recycle destination is not under $Recycle.Bin: {recycle_path!r}")
    if not evidence["recycle_item_observed_before_undo"]:
        return fail(f"recorded recycle destination does not exist on disk before undo: {recycle_path!r}")

    watcher_state["errors"] = []

    def handle_undo_confirm(widget) -> None:
        yes_button = widget.button(QMessageBox.StandardButton.Yes)
        QTest.mouseClick(yes_button, Qt.MouseButton.LeftButton)
        evidence["undo_confirmation_accepted"] = True
        click_when_modal(
            lambda widget: isinstance(widget, QMessageBox) and widget.windowTitle() == "Undo delete complete",
            handle_undo_complete,
        )

    def handle_undo_complete(widget) -> None:
        ok_button = widget.button(QMessageBox.StandardButton.Ok)
        QTest.mouseClick(ok_button, Qt.MouseButton.LeftButton)

    click_when_modal(
        lambda widget: isinstance(widget, QMessageBox) and widget.windowTitle() == "Undo delete",
        handle_undo_confirm,
    )
    window.undo_delete_action.trigger()
    evidence["undo_action_triggered"] = True
    for _ in range(5):
        app.processEvents()

    if watcher_state["errors"]:
        return fail("undo flow dialog automation timed out: " + "; ".join(watcher_state["errors"]))
    if not evidence["undo_confirmation_accepted"]:
        return fail("undo delete confirmation dialog was never observed/accepted")

    window.grab().save(str(isolated_root / "after-undo.png"))

    evidence["source_restored_after_undo"] = deleted_original.is_file()
    if not evidence["source_restored_after_undo"]:
        return fail(f"deleted copy was not restored to its original path: {deleted_original}")

    restored_hash = sha256_of(deleted_original)
    evidence["restored_hash_matches"] = restored_hash == expected_sha256
    if not evidence["restored_hash_matches"]:
        return fail("restored file's SHA-256 does not match the deletion log's recorded hash")

    restored_record = repository.get_by_path(str(deleted_original))
    evidence["catalog_status_restored"] = bool(restored_record and restored_record.status == "indexed")
    if not evidence["catalog_status_restored"]:
        return fail("catalog record status was not restored to 'indexed' after undo")

    evidence["status"] = "passed"
    write_evidence()
    log("All production Delete/Undo Delete UI flows verified successfully.")

    window.close()
    repository.close()
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        evidence = run(args.round_dir, repo_root=args.repo_root, state_root=args.state_root)
    except GuardError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except Exception:  # pragma: no cover - top-level safety net
        traceback.print_exc()
        return 1
    return 0 if evidence.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
