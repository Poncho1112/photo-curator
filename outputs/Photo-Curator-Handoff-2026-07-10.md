# Project Handoff

Date: 2026-07-10

## Project Goal

Deliver Photo Curator v0.1 as a polished, local-first PySide6 desktop application for safely indexing, browsing, searching, reviewing, renaming, and undoing filename changes across copied photo libraries.

## Main Folder

`C:\Users\Poncho\photo-curator`

## Key Files

- `app/ui/main_window.py` — main UI, menus, preferences, workers, status, rename/undo flow
- `app/controllers/library_controller.py` — indexing, filters, rename selection, manifests, rename and undo coordination
- `app/views/photo_grid.py` — multi-selection thumbnail browser and rubber-band behavior
- `app/views/photo_preview.py` — EXIF-oriented preview and metadata actions
- `app/views/folder_panel.py` — folder/filter UI and indexed/missing counts
- `app/widgets/photo_tile.py` — thumbnail tile, badges, and explicit rename checkbox
- `app/workers/scan_worker.py` — cancelable photo scanning
- `app/workers/thumbnail_worker.py` — cooperatively cancelable thumbnail generation
- `engine/database/repository.py` — SQLite persistence, migration, search, and safe catalog-row deletion
- `README.md` and `docs/Architecture.md` — user and architecture documentation

## Current Capabilities

- Launch with `python -m app` or `run_photo_curator.bat`
- Add multiple folders and scan recursively outside the UI thread
- Index/update photos in SQLite while preserving missing records
- Browse cached thumbnails at Small, Medium, or Large sizes
- Ordinary Ctrl/Shift/rubber-band selection remains separate from rename checkbox selection
- Preview EXIF-oriented images and structured metadata
- Search and filter by indexed metadata, duplicates, missing, renamed, or rename-selected state
- Review/export rename manifests, safely rename selected files, and undo batches
- System, Light, and Dark themes persist through explicit INI-backed `QSettings`
- Context menus, keyboard shortcuts, dynamic rename count, folder counts, and persistent status summary

## Important Decisions

- Do not rewrite the tested engine/database architecture.
- Scanning is read-only and never renames, moves, or deletes files.
- Rename selection is explicit and independent of ordinary UI selection.
- Runtime data lives under the platform application-data folder, not the repository.
- Production preferences use `<application-data>/settings.ini` through `QSettings.IniFormat`.
- Tests inject fresh `QSettings` instances pointing at the same temporary INI file.
- Thumbnail cancellation uses `threading.Event` because a queued Qt slot cannot run while the synchronous worker loop occupies its thread.

## Current Data / Status

- Working tree is intentionally uncommitted and contains the complete Milestone 1 plus UI/UX improvements.
- Latest native result reported before the newest fixes: `43 passed, 3 failed`; all three failures were QSettings persistence tests.
- QSettings was subsequently changed to injectable explicit INI storage with synchronized writes and a separate-instance regression test.
- Five more focused tests were added for thumbnail cancellation/lifecycle and folder counts.
- Expected native suite size is approximately 52 tests; rerun natively to confirm.
- Latest fallback verification: `30 passed, 4 skipped` under Python 3.12. The skipped modules require native PySide6.
- `compileall` and `git diff --check` pass in the fallback environment.
- The managed sandbox cannot execute `.venv` because its Python 3.14 base executable is outside the workspace and returns `Access is denied`.

## Known Bugs Fixed

- Unsafe filename sanitization and target overwrite refusal
- Exact duplicate detection with SHA-256
- Pre-rename undo logging and safe restoration
- Missing-file handling and canceled-scan missing-state corruption
- Rename selection persistence across filtering
- Native Windows QSettings backend/path mismatch
- Live thumbnail `QThread` risk during close
- Visible thumbnails stranded after filters changed during generation
- Status summary hidden by transient status messages
- Repeated path normalization/stat calls during folder-count refresh
- Duplicate Escape shortcut documentation

## Known Cautions

- Do not discard or reset the dirty worktree; it contains user-requested changes.
- `engine/database/__pycache__/repository.cpython-314.pyc` is tracked and modified; decide deliberately whether to remove tracked bytecode in a separate cleanup.
- Window close now waits for the current thumbnail operation after requesting cancellation. Cancellation occurs between files, not within Pillow decoding.
- Native Qt rendering, shutdown, and all new offscreen tests still need a successful Python 3.14 run outside the managed sandbox.
- Do not add AI tagging, OCR, face recognition, cloud services, automatic organization, React, Tauri, or FastAPI to this milestone.

## Next Best Improvements

1. Run the full native Python 3.14 suite and confirm the expected test count.
2. Launch the app against a temporary copied photo folder and smoke-test scan, filters, selection, rename review, manifest, rename, and undo.
3. Specifically verify close during a long thumbnail batch and filter changes while thumbnails load.
4. Review whether tracked `__pycache__` files should be removed in a separate, approved cleanup.
5. Commit the milestone intentionally after native verification.

## Commands

```powershell
cd C:\Users\Poncho\photo-curator
.\.venv\Scripts\python.exe -m pytest tests\test_ui_integration.py -v --tb=short
.\.venv\Scripts\python.exe -m pytest -v --tb=short
.\.venv\Scripts\python.exe -m compileall app engine
git diff --check
.\.venv\Scripts\python.exe -m app
```

Fallback used inside the sandbox:

```powershell
$env:PYTHONPATH = "$PWD\.venv\Lib\site-packages"
& 'C:\Program Files\Inkscape\bin\python.exe' -m pytest -v --tb=short -p no:cacheprovider --basetemp "$PWD\test-tmp-review-fixes"
```

## Fernando Preferences

- Concise, direct communication with no fluff
- Obsidian-ready Markdown
- Preserve project state and backups before risky changes
- Make practical changes directly when authorized
- No second location or capital spending over $20,000 for 2026 business guidance
- ClearFusionLab success target: $1.5M revenue, crossover sales, employee retention, and debt reduction

## Memory Candidates

- **27/30** — Photo Curator Milestone 1 is a local-first PySide6 desktop application with safe SQLite indexing, explicit rename review, CSV manifests, batch undo, multi-selection thumbnails, persisted themes, and cooperative background-worker shutdown. Native Python 3.14 verification remains the immediate next step.

