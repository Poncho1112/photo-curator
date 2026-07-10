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

