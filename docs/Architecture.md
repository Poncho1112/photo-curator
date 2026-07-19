# Photo Curator Architecture

## Design goals

- Local-first and offline
- No filesystem mutation during scanning
- Explicit selection and review before renaming or deleting
- Pre-rename and pre-delete durable JSONL records
- No-overwrite rename and undo operations
- Testable controller behavior independent of a visible GUI

## Runtime flow

```text
PySide6 MainWindow
  ├─ LibraryController ─ SQLite PhotoRepository
  ├─ ScanWorker/QThread ─ scanner + Pillow metadata + SHA-256 + naming
  ├─ ThumbnailWorker/QThread ─ Pillow thumbnail cache
  ├─ Rename review ─ RenameService ─ UndoService
  └─ Delete review ─ DeleteService (send2trash / injected trash) ─ UndoDeleteService
```

## Application layer

- `app/main.py` creates application-data paths, logging, SQLite, controller, and the main window.
- `app/controllers/library_controller.py` owns filter state, explicit rename selections, indexing, duplicate groups, manifests, rename coordination, undo coordination, duplicate-deletion review validation, and Undo Delete coordination.
- `app/ui/main_window.py` coordinates Qt actions and background threads, including the `delete_duplicates_flow` and `undo_delete_flow` menu actions.
- `app/views/` contains folder/filter, thumbnail, preview, rename-review, and delete-review views.
- `app/widgets/` contains reusable checkbox-based photo tiles, metadata, and progress components.
- `app/workers/` contains cancelable scan work and thumbnail generation adapters.

## Core services

- `engine/scanner/` discovers supported files recursively.
- `engine/metadata/` reads EXIF values and capture timestamps.
- `engine/database/` persists photo records and migrates the v0.1 schema additively.
- `engine/duplicates/` streams files through SHA-256.
- `engine/thumbnails/` creates orientation-corrected cached previews outside SQLite.
- `engine/search/` queries indexed searchable fields.
- `engine/rename/` generates names, safely renames, writes undo logs, and restores names.
- `engine/delete/` picks survivors, hash-verified moves to the Recycle Bin, writes a deletion log, and restores from it. See [[#Duplicate deletion]].

## Persistence

See [[../README#Application data|Application data]]. SQLite stores paths and metadata, including missing records and `status="deleted"` rows. Thumbnail JPEGs are content-addressed by SHA-256 and stored separately. Every rename batch has its own JSONL undo file; every delete batch has its own JSONL deletion log under `undo/delete-*.jsonl`.

## Threading

Scanning and thumbnail generation run on `QThread` workers. Cancellation sets a thread-safe event checked between scan files. SQLite updates happen through the controller on the GUI thread after worker completion, avoiding cross-thread connection use.

## Safety boundaries

- Scanning is read-only.
- Rename selection is independent of visual selection and survives filtering.
- Review confirmation is disabled for missing sources or existing targets.
- The core rename and undo services repeat overwrite checks immediately before mutation.
- Duplicate deletion is the only path that removes files; see [[#Duplicate deletion]].
- Technical errors go to the application log; the UI shows concise messages.

## Duplicate deletion

Reviewed duplicate deletion is the only feature that removes files, built around survivor retention, hash verification at delete time, and Recycle Bin (not permanent) erasure:

- `LibraryController.delete_review()` groups exact duplicates by SHA-256, picks one survivor per group via `choose_survivor` (`engine/delete/keep_policy.py`), and refuses to produce a review that does not retain exactly one survivor per group.
- `LibraryController._validate_delete_review()` re-checks the live catalog before execution and rejects any review whose survivor or targets no longer match the live group, including forged reviews that would delete the last copy.
- `DeleteService.delete_paths()` (`engine/delete/delete_service.py`) recomputes each file's SHA-256 immediately before trashing and skips any file whose hash differs from the indexed value. The default trash function is `_send_to_trash`, which calls the installed `send2trash.send2trash()`; there is no `os.remove` path.
- Each trashed file is appended to a per-batch JSONL deletion log recording the original path, SHA-256, `trashed_to` destination, and UTC timestamp. Catalog rows for trashed files are marked `status="deleted"`, not removed, so they stay visible for audit and are excluded from future duplicate grouping.
- `UndoDeleteService.restore_all()` (`engine/delete/undo_delete_service.py`) reads the latest deletion log in reverse, refuses to overwrite existing files, and restores each trashed file to its original path; unrestorable entries are rewritten to the log.
- `LibraryController.undo_delete()` flips restored rows back to `status="indexed"` and re-points `last_delete_log` at the next remaining deletion log.

### Known blocker: default `send2trash` is not reversible through Undo Delete

The default production trash provider is `_send_to_trash` in `engine/delete/delete_service.py`, which calls the installed `send2trash.send2trash()`. That function returns `None`, so `DeleteService` writes the deletion-log entry with `trashed_to: null`. `UndoDeleteService.restore_all()` reads `trashed_to` from the log and refuses restoration when it is `None` (it cannot locate the trashed file). Default production Recycle Bin deletions are therefore **not currently reversible** through the in-app Undo Delete action.

The green Undo Delete tests (`tests/test_delete_service.py`, `tests/test_delete_controller.py`) and the user-confirmed M3 copy-folder restore proof use an **injected** trash provider that returns a destination path, which the default `send2trash` provider does not. Production restoration of default `send2trash` Recycle Bin moves is a known blocker until a recoverable destination integration is implemented. This blocker does not affect survivor retention, the pre-delete SHA-256 recheck, or the Recycle Bin (non-permanent) deletion guarantee.

## Selection model

The thumbnail browser deliberately maintains two independent concepts:

- Qt extended selection controls focus, Ctrl/Shift selection, and rubber-band selection.
- `LibraryController.rename_selection` stores explicit checkbox choices by database ID and survives filtering.

Double-click activates preview. Space maps the current ordinary selection into checkbox state. Rebuilding the visible filtered grid restores both applicable states.

The selection-aware context menu targets the entire ordinary selection when the clicked tile is already selected; otherwise it first replaces the ordinary selection with that tile. Removing a missing record deletes only its SQLite catalog row and never touches the filesystem. `PhotoGrid.keyPressEvent` swallows `Qt.Key.Key_Delete` so the grid performs no action on it; duplicate deletion is reachable only through the **Library → Delete Duplicates…** menu action.

## UI preferences

`QSettings` persists the System/Light/Dark theme and Small/Medium/Large thumbnail size. System theme retains the platform palette, while Light and Dark apply coherent application styles covering menus, toolbars, tiles, metadata groups, dialogs, and the status bar.

## Tests

Core tests cover naming, SQLite, duplicate hashing, rename, and undo. Milestone tests cover controller loading and filtering, selection persistence, conflict review, manifests, missing records, scan cancellation/error isolation, offscreen preview integration, thumbnail lifecycle (close joins the active thread and filter changes queue a refresh), index batching, incremental re-scan, and reviewed duplicate deletion (keep-policy, last-copy protection, pre-delete hash re-check, trash, undo with an injected provider, and the delete-review UI flow). The native suite is 89 tests; the delete tests inject a trash function that returns a destination path so they exercise the restorable path that the default `send2trash` provider does not.
