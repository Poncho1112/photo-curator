# Photo Curator

Photo Curator is a local-first PySide6 desktop application for safely indexing, browsing, searching, renaming, and reviewing exact-duplicate deletion across copied photo collections.

Renames are review-first, no-overwrite, and reversible from a per-batch JSONL log. Exact-duplicate deletion is review-first, survivor-retaining, hash-verified, and moves copies to the OS Recycle Bin; it never permanently erases files. The **Undo Delete** action and `UndoDeleteService` exist and pass tests with an injected trash provider, but production restoration of default `send2trash` Recycle Bin moves is a known blocker until a recoverable destination integration is implemented (see [Deleting exact duplicates](#deleting-exact-duplicates) and [[docs/Architecture#Duplicate deletion|Architecture → Duplicate deletion]]).

The project deliberately excludes AI tagging, OCR, face recognition, cloud services, automatic organization, and natural-language search. Near-duplicate and perceptual-hash detection are out of scope; only exact SHA-256 duplicates can be deleted, and deletion never bypasses the Recycle Bin.

## Requirements

- Python 3.11 or newer
- Windows 10/11, macOS, or a Linux desktop supported by PySide6

## Installation

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

## Run

```powershell
python -m app
```

On Windows, `run_photo_curator.bat` activates the repository virtual environment automatically and reports missing dependencies.

## Safe first use

1. Make a backup and work with a copied photo folder first.
2. Add the copied folder and select **Scan**. Scanning never renames, moves, or deletes files.
3. Inspect thumbnails, metadata, proposed names, duplicate markers, and missing-file markers.
4. Double-click a tile or focus it and press Space to explicitly select it for rename.
5. Export the CSV manifest and review the rename table.
6. Confirm only when every row is conflict-free.
7. Use **Undo Last Batch** to restore the latest successfully renamed batch.

Photo Curator never overwrites an existing destination. Failed or missing files remain indexed for review.

## Browsing and selection

- Double-click a thumbnail to open its larger preview.
- Click, Ctrl-click, Shift-click, or drag across empty grid space to create an ordinary UI selection.
- Use the visible **Rename** checkbox—or press Space—to add the ordinary selection to the separate rename plan.
- Right-click a tile to preview, open/reveal it, mark/remove/toggle rename selection, copy its path or filename, isolate its exact-duplicate group, or remove a missing catalog record.
- Choose Small, Medium, or Large thumbnails from the toolbar.
- Choose System, Light, or Dark under **View → Theme**. Theme and thumbnail size persist between sessions through `QSettings`.
- The status bar separately reports visible photos, ordinary selection, rename marks, duplicates, and missing records.

## Deleting exact duplicates

1. Scan a copied folder so exact duplicates are grouped by SHA-256.
2. Choose **Library → Delete Duplicates…** to open the review dialog.
3. The review lists each group with one survivor marked KEEP and every other copy marked DELETE, plus file sizes and the reclaimable total.
4. Confirm with **Move N copies to Recycle Bin**. Files are moved to the OS Recycle Bin via `send2trash`, not permanently erased.
5. Before each file is trashed, its SHA-256 is recomputed and compared with the indexed hash. A file that changed since indexing is skipped and reported, never deleted on a stale hash.
6. Each group is guaranteed at least one survivor at the data level — the controller refuses to produce or accept a review that would delete the last copy of a group, including forged reviews.
7. Catalog rows for trashed files are marked `status="deleted"` and stay visible for audit; the on-disk file is moved to the Recycle Bin.

### Undo Delete — present, but not yet reversing default Recycle Bin moves

The **Edit → Undo Delete** action and `engine/delete/undo_delete_service.py` exist and pass tests. Each trashed file is appended to a per-batch JSONL deletion log recording the original path, SHA-256, `trashed_to` destination, and UTC timestamp; `UndoDeleteService.restore_all()` reads that log in reverse, refuses to overwrite existing files, and restores each trashed file to its original path.

However, the default production trash provider is `_send_to_trash` in `engine/delete/delete_service.py`, which calls the installed `send2trash.send2trash()`. That function returns `None`, so the deletion log records `trashed_to: null`. `UndoDeleteService` refuses restoration when `trashed_to` is `None`, so default production Recycle Bin deletions are **not currently reversible** through the in-app Undo Delete action. The green Undo Delete tests (and the user-confirmed M3 copy-folder restore proof) use an injected trash provider that returns a destination path, which the default provider does not. Production restoration of default `send2trash` Recycle Bin moves is a known blocker until a recoverable destination integration is implemented.

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| Ctrl+O | Add folder |
| F5 | Start scan |
| Ctrl+F | Focus search |
| Space | Toggle rename checkbox for selected tiles |
| Enter | Preview focused tile |
| Ctrl+A | Select all visible tiles |
| Ctrl+R | Review and rename selected photos |
| Ctrl+E | Export manifest |
| Ctrl+Z | Undo latest rename batch |
| Ctrl+Shift+R | Remove selected tiles from the rename set |
| Esc | Cancel an active scan, otherwise clear ordinary selection |
| Delete | No action in the grid; duplicate deletion is review-only via **Library → Delete Duplicates…** |

`Delete Duplicates…` (Library menu) and `Undo Delete` (Edit menu) are menu actions only; no keyboard shortcut is bound to either. `PhotoGrid.keyPressEvent` swallows `Qt.Key.Key_Delete` so the grid performs no action on it.

## Tests

```powershell
python -m pytest -v --tb=short
git diff --check
```

Tests use temporary folders and SQLite databases; they do not access a real photo library. Qt tests use the offscreen platform. The delete tests inject a trash function that returns a destination path, so no test touches the real Recycle Bin and the Undo Delete tests exercise the restorable path that the default `send2trash` provider does not provide.

## Application data

Runtime data is not written to this repository.

- Windows: `%LOCALAPPDATA%\PhotoCurator`
- macOS/Linux fallback: `$XDG_DATA_HOME/PhotoCurator` or `~/.local/share/PhotoCurator`

The directory contains `catalog.sqlite3`, `thumbnails/`, `manifests/`, `undo/`, and `photo-curator.log`. Deletion logs live under `undo/` as `delete-*.jsonl`.

## Current limitations

- Exact duplicates are identified by SHA-256; near-duplicate / perceptual-hash detection is not included.
- Search is structured text matching over indexed metadata, not natural language.
- One active scan is supported at a time.
- Rename Undo operates on the latest eligible rename log; Undo Delete operates on the latest deletion log.
- Deletion moves copies to the OS Recycle Bin and never permanently erases or empties the bin.
- Default production Recycle Bin deletions are not currently reversible through Undo Delete (see above); only an injected trash provider that returns a destination path has been verified to restore.
- Renames stay in the original folder; Photo Curator performs no automatic moves.

See [[docs/Architecture|Architecture]] for component details, [[docs/Milestone-2-Scope|Milestone 2]] and [[docs/Milestone-3-Scope|Milestone 3]] for scope and status, and [[outputs/Photo-Curator-Status-2026-07-19|Status 2026-07-19]] for the current status note.
