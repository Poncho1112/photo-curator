# Photo Curator

Photo Curator is a local-first PySide6 desktop application for safely indexing, reviewing, renaming, and undoing filename changes across copied photo collections.

Milestone 1 deliberately excludes AI tagging, OCR, face recognition, cloud services, automatic organization, and natural-language search.

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
| Ctrl+Z | Undo latest batch |
| Ctrl+Shift+R | Remove selected tiles from the rename set |
| Esc | Cancel an active scan, otherwise clear ordinary selection |
| Delete | No action; Photo Curator never deletes photos |

## Tests

```powershell
python -m pytest -v --tb=short
git diff --check
```

Tests use temporary folders and SQLite databases; they do not access a real photo library. Qt tests use the offscreen platform.

## Application data

Runtime data is not written to this repository.

- Windows: `%LOCALAPPDATA%\PhotoCurator`
- macOS/Linux fallback: `$XDG_DATA_HOME/PhotoCurator` or `~/.local/share/PhotoCurator`

The directory contains `catalog.sqlite3`, `thumbnails/`, `manifests/`, `undo/`, and `photo-curator.log`.

## Current limitations

- Exact duplicates are identified by SHA-256; similarity detection is not included.
- Search is structured text matching over indexed metadata, not natural language.
- One active scan is supported at a time.
- Undo operates on the latest eligible application rename log.
- Renames stay in the original folder; Photo Curator performs no automatic moves.

See [[docs/Architecture|Architecture]] for component details.
