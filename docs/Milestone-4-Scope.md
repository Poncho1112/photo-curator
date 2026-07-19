# Milestone 4 — Recoverable Trash Destinations for Undo Delete

Date scoped: 2026-07-19
Status: scoped — not started. Milestone 3 (reviewed duplicate deletion) is merged; this milestone closes its known blocker.

## Goal

Make the in-app **Undo Delete** action actually reverse the deletions users
perform through normal use, not just the injected-provider path exercised by
tests. Today, only test runs with a fake trash function that returns a
destination path are restorable; every real deletion through the default
`send2trash` provider is not.

## The problem

`DeleteService`'s default trash function (`_send_to_trash` in
`engine/delete/delete_service.py`) calls `send2trash.send2trash()`, which
returns `None` on every platform by design. `DeleteService` therefore writes
`trashed_to: null` to the deletion log for every real-world delete.
`UndoDeleteService.restore_all()` refuses to restore any entry whose
`trashed_to` is `None`, because it has no location to restore from. The fix
is not in the undo logic — it already works correctly given a real path
(proven by the green injected-provider tests) — the fix is capturing a real,
verifiable trash destination at delete time.

## Why this milestone (and not a smaller patch)

There is no cross-platform API that both (a) moves a file to the native OS
Recycle Bin/Trash and (b) reports back where it went — that's exactly the
gap `send2trash` was written to paper over, at the cost of the returned
path. Closing it correctly requires a native call per platform, so this is
scoped as its own milestone rather than a one-line change.

## In scope

### 1. Trash provider abstraction
Replace the single `_send_to_trash` function with a small provider package,
`engine/delete/trash_providers/`, selected at runtime by `platform.system()`.
`DeleteService`'s public interface is unchanged — it already accepts any
`trash_fn: Callable[[Path], Path | None]`.

### 2. macOS provider
Call `NSFileManager.trashItemAtURL_resultingItemURL_error_` directly via
`pyobjc` instead of going through `send2trash`. This is the same underlying
Cocoa call `send2trash` uses on macOS; it returns the resulting trashed URL
by design (it renames on collision and reports the final name), so no
capture problem exists once we call it ourselves instead of through a
wrapper that discards the result.

### 3. Windows provider
Primary path: `IFileOperation` via `pywin32`, with `FOFX_RECYCLEONDELETE`
set, using an `IFileOperationProgressSink` that implements `PostDeleteItem`
— Windows calls this with the newly created (recycled) item, giving a real
restorable path.

Fallback, if `pywin32` is not installed or COM initialization fails on the
Qt thread: move the file into an app-managed quarantine folder
(`%LOCALAPPDATA%\PhotoCurator\trash\<batch-id>\`) instead of the system
Recycle Bin. This preserves the "never lose a file, always undoable"
guarantee without the native API, at the cost of the file not appearing in
Explorer's Recycle Bin. This trade-off must be surfaced to the user (status
bar / log message identifying which provider handled a given batch) and
documented in the README, not silently substituted.

### 4. Linux provider
Do not rely on `send2trash`'s freedesktop.org fallback. Implement the XDG
trash spec directly — `files/` + `info/*.trashinfo` sidecar, with our own
collision-safe naming — so the destination path is known with certainty
because we perform the move ourselves rather than delegating to a function
that discards it.

### 5. Deletion log / Undo Delete error messaging
No change to the log schema or to `UndoDeleteService.restore_all()`'s
restore logic. Improve the error string returned for unrestorable entries
so the two distinct cases are distinguishable instead of both reading
"trashed file does not exist; cannot restore":
- `trashed_to` is `None` (deleted before this milestone shipped, or the
  Windows fallback path was itself unavailable) → "no recoverable
  destination was recorded for this deletion."
- `trashed_to` was recorded but the file is no longer there (Recycle
  Bin/Trash emptied by the user outside the app) → "trashed file no longer
  exists; it may have been emptied from the Recycle Bin."

### 6. Packaging
Add platform-conditional optional dependencies in `pyproject.toml`:
`pywin32; sys_platform == "win32"` and `pyobjc-framework-Cocoa;
sys_platform == "darwin"`. Keep `send2trash` only as a last-resort import
guard if a provider fails to initialize at runtime, not as the default path.

## Out of scope

- Near-duplicate / perceptual-hash detection (unchanged from Milestone 3).
- Permanent deletion, emptying the Recycle Bin, or any bypass of it.
- Restoring entries logged by prior versions of the app with `trashed_to:
  null` already written — those remain unrestorable; this milestone fixes
  new deletions going forward only.
- Any change to keep-policy selection, the delete-review dialog, or
  pre-delete SHA-256 verification (all Milestone 3, unchanged).

## Open decision

The Windows fallback intentionally trades "literally in the Recycle Bin"
for "guaranteed undoable" when `pywin32` is unavailable. An alternative is
to treat a missing `pywin32` as a hard error (block deletion, prompt to
install) instead of silently quarantining elsewhere. This should be decided
before implementation starts.

## Acceptance criteria

- A file deleted through **Library → Delete Duplicates…** under default
  settings (no injected test provider) is restorable via **Edit → Undo
  Delete** to its original path, on Windows (with and without `pywin32`
  installed) and on macOS and Linux where testable.
- Existing 89-test suite stays green; new tests cover each provider
  (mocked platform calls) plus the Windows no-`pywin32` fallback.
- A file removed from the OS Recycle Bin/Trash between delete and undo
  (outside the app) produces the distinct "emptied" error, not a crash.
- The known-blocker note is removed from `README.md`,
  `docs/Architecture.md`, and `outputs/Photo-Curator-Status-2026-07-19.md`
  once verified, with a superseding status note added the same way the
  2026-07-10 handoff was superseded.

## Verification approach

Same discipline as Milestones 1–3: native suite plus a real end-to-end pass
on a *copy* of real files (never the original) — trash a handful of copy
photos through the actual UI, confirm they land in the real Recycle
Bin/Trash, then Undo Delete and confirm they're restored to their original
paths. Repeat across each of the 3 computers this project is worked from,
recording which trash provider (native vs. Windows fallback) handled each
run.
