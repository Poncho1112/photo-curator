# Photo Curator — Current Status

Date: 2026-07-19

This note is the current project status and supersedes [[Photo-Curator-Handoff-2026-07-10|the 2026-07-10 handoff]] for current status. The 2026-07-10 handoff is retained for history and is not rewritten.

## Milestones

- **Milestone 1** — shipped. Local-first PySide6 indexing, browsing, searching, rename review, manifests, batch rename undo, themes, and thumbnail lifecycle.
- **Milestone 2** — merged. See [[../docs/Milestone-2-Scope|Milestone 2 scope]].
- **Milestone 3** — merged. See [[../docs/Milestone-3-Scope|Milestone 3 scope]].

## Test status

- Native suite: **126 passed** under `python -m pytest -q`.
- Qt tests run on the offscreen platform; tests use temporary folders and injected trash functions so no test touches a real photo library or the real Recycle Bin.

## Duplicate deletion (Milestone 3)

Reviewed duplicate deletion is the only path that removes files. It is review-first, survivor-retaining, hash-verified, and Recycle Bin-only:

- **Library → Delete Duplicates…** opens a review dialog listing each exact-duplicate (SHA-256) group with one survivor marked KEEP and every other copy marked DELETE, plus sizes and the reclaimable total.
- `LibraryController._validate_delete_review()` re-checks the live catalog before execution and refuses any review that would not retain exactly one survivor per group, including forged reviews.
- `DeleteService.delete_paths()` (`engine/delete/delete_service.py`) recomputes each file's SHA-256 immediately before trashing and skips any file whose hash differs from the indexed value. The default trash function is platform-selected: `_send_to_trash_windows` (native `IFileOperation` Recycle Bin move) on Windows, and `_send_to_trash_send2trash` (calls the installed `send2trash.send2trash()`) elsewhere; there is no permanent-erase path.
- Each trashed file is appended to a per-batch JSONL deletion log under `undo/delete-*.jsonl` recording the original path, SHA-256, `trashed_to` destination, and UTC timestamp. Catalog rows are marked `status="deleted"`, not removed.

### Undo Delete — restores on Windows; not yet on macOS/Linux

The **Edit → Undo Delete** action and `engine/delete/undo_delete_service.py` exist and pass tests. `UndoDeleteService.restore_all()` reads the deletion log in reverse, refuses to overwrite existing files, and restores each trashed file to its original path.

On Windows, the default production trash provider is `_send_to_trash_windows` in `engine/delete/delete_service.py`, which calls `engine/delete/windows_recycle_bin.py`'s `send_to_recycle_bin` — a native `IFileOperation` Recycle Bin move that captures and returns the exact recycled path. That path is written to the deletion log as `trashed_to`, so Windows deletions restore through the in-app Undo Delete action by default.

On macOS and Linux, the default production trash provider is still `_send_to_trash_send2trash`, which calls the installed `send2trash.send2trash()`. That function returns `None`, so the deletion log records `trashed_to: null`. `UndoDeleteService` refuses restoration when `trashed_to` is `None`, so default production trash moves on macOS/Linux are **not currently reversible** through the in-app Undo Delete action. This remains a **known blocker** until a recoverable destination integration is implemented for those platforms.

The green Undo Delete tests (`tests/test_delete_service.py`, `tests/test_delete_controller.py`) and the user-confirmed M3 copy-folder restore proof also use an **injected** trash provider that returns a destination path, exercising the same restorable path the Windows native backend now provides by default. The injected-provider Undo proof is distinct from the verified survivor/deletion behavior: the user confirmed the M3 real-world copy-folder verification of survivor retention and deletion (every group retained its chosen survivor, space was reclaimed), while the Undo Delete restore path beyond Windows is only proven under the injected provider.

See [[../docs/Architecture#Duplicate deletion|Architecture → Duplicate deletion]] for the component breakdown.

## Performance (Milestone 2)

10k synthetic-library benchmark artifacts under `benchmarks/`:

| Phase | Baseline (`baseline-2026-07-11-10k.json`) | After incremental re-scan (`after-incremental-rescan-2026-07-11-10k.json`) |
|---|---|---|
| First index | 31.96 s | 0.31 s |
| Unchanged re-scan | 103.83 s | 3.84 s |

Landed: index batching into one transaction, incremental re-scan (skip SHA-256 when path/size/mtime are unchanged), set-based `mark_missing_except`, visible-first thumbnail priority, and the thumbnail cache cap.

## Offscreen desktop smoke

An executed offscreen desktop smoke passed and is recorded as automated, not visual: scan → filter → rename + undo, close during thumbnails, and filter during thumbnails. In the final verifier, window close completed in 0.012 s and filter apply in 0.011 s. These map to the in-repo tests `test_close_cancels_and_joins_active_thumbnail_thread` and `test_active_thumbnail_batch_queues_refresh_for_latest_visible_records`.

## Honest caveats (not yet done)

- Full 10k UI acceptance against a real archive is not yet recorded in-repo. Throughput is benchmarked; the real-library UI run is not.
- Deletion is exact-duplicate only (SHA-256). Near-duplicate / perceptual-hash detection is not included.
- Deletion never permanently erases or empties the Recycle Bin. There is no permanent-delete path.
- Default production trash moves on macOS/Linux are not currently reversible through Undo Delete; Windows deletions restore by default via the native `IFileOperation` backend, and an injected trash provider that returns a destination path also restores on any platform. This `trashed_to: null` / `send2trash`-returns-`None` case remains a known blocker on macOS/Linux.

## Commands

```powershell
python -m pytest -q
python -m app
```

## Pointers

- [[../README|README]] — user-facing overview, safe first use, deleting exact duplicates, shortcuts.
- [[../docs/Architecture|Architecture]] — runtime flow, services, safety boundaries, duplicate deletion, and the Undo Delete known blocker.
- [[../docs/Milestone-2-Scope|Milestone 2 scope]] and [[../docs/Milestone-3-Scope|Milestone 3 scope]] — scope, acceptance, and verification outcomes.
- [[../docs/Performance-Audit-2026-07-11|Performance audit 2026-07-11]] — hotspot analysis.
- [[Photo-Curator-Handoff-2026-07-10|Handoff 2026-07-10]] — superseded for current status; retained for history.
