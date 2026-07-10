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
- `app/widgets/` contains reusable tile, metadata, and progress components.
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

## Tests

Core tests cover naming, SQLite, duplicate hashing, rename, and undo. Milestone tests cover controller loading and filtering, selection persistence, conflict review, manifests, missing records, scan cancellation/error isolation, and offscreen preview integration.

