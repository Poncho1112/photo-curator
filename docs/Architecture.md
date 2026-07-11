# Photo Curator Architecture

## Design goals

- Local-first and offline
- No filesystem mutation during scanning
- Explicit selection and review before renaming
- Pre-rename durable JSONL records
- No-overwrite rename and undo operations
- Testable controller behavior independent of a visible GUI

## Runtime flow

```text
PySide6 MainWindow
  ├─ LibraryController ─ SQLite PhotoRepository
  ├─ ScanWorker/QThread ─ scanner + Pillow metadata + SHA-256 + naming
  ├─ ThumbnailWorker/QThread ─ Pillow thumbnail cache
  └─ Rename review ─ RenameService ─ UndoService
```

## Application layer

- `app/main.py` creates application-data paths, logging, SQLite, controller, and the main window.
- `app/controllers/library_controller.py` owns filter state, explicit rename selections, indexing, duplicate groups, manifests, rename coordination, and undo coordination.
- `app/ui/main_window.py` coordinates Qt actions and background threads.
- `app/views/` contains folder/filter, thumbnail, preview, and rename-review views.
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

## Persistence

See [[../README#Application data|Application data]]. SQLite stores paths and metadata, including missing records. Thumbnail JPEGs are content-addressed by SHA-256 and stored separately. Every rename batch has its own JSONL undo file.

## Threading

Scanning and thumbnail generation run on `QThread` workers. Cancellation sets a thread-safe event checked between scan files. SQLite updates happen through the controller on the GUI thread after worker completion, avoiding cross-thread connection use.

## Safety boundaries

- Scanning is read-only.
- Rename selection is independent of visual selection and survives filtering.
- Review confirmation is disabled for missing sources or existing targets.
- The core rename and undo services repeat overwrite checks immediately before mutation.
- Technical errors go to the application log; the UI shows concise messages.

## Selection model

The thumbnail browser deliberately maintains two independent concepts:

- Qt extended selection controls focus, Ctrl/Shift selection, and rubber-band selection.
- `LibraryController.rename_selection` stores explicit checkbox choices by database ID and survives filtering.

Double-click activates preview. Space maps the current ordinary selection into checkbox state. Rebuilding the visible filtered grid restores both applicable states.

The selection-aware context menu targets the entire ordinary selection when the clicked tile is already selected; otherwise it first replaces the ordinary selection with that tile. Removing a missing record deletes only its SQLite catalog row and never touches the filesystem.

## UI preferences

`QSettings` persists the System/Light/Dark theme and Small/Medium/Large thumbnail size. System theme retains the platform palette, while Light and Dark apply coherent application styles covering menus, toolbars, tiles, metadata groups, dialogs, and the status bar.

## Tests

Core tests cover naming, SQLite, duplicate hashing, rename, and undo. Milestone tests cover controller loading and filtering, selection persistence, conflict review, manifests, missing records, scan cancellation/error isolation, and offscreen preview integration.
