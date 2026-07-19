# Milestone 3 — Reviewed Duplicate Deletion

Date scoped: 2026-07-11
Status: merged — reviewed duplicate deletion landed; the user confirmed the real-world copy-folder verification of survivor/deletion behavior. In-app Undo Delete exists and passes injected-provider tests, but production restoration of default `send2trash` Recycle Bin moves is a known blocker.

## Goal

Let the user reclaim disk space from exact duplicates safely. On the real
archive (`H:\Pictures`, ~25.7k photos) the catalog identified 12,747
duplicate files in 6,257 groups — about 13.6 GB reclaimable if one copy
per group is kept. Milestone 1/2 deliberately shipped no delete path; this
milestone adds one, and its entire design is about doing so without ever
losing a photo the user meant to keep.

## The safety line this milestone must not cross

Photo Curator's founding rule is that indexing never destroys data. Adding
deletion is the first feature that removes files, so it carries the
strictest safeguards in the app:

1. **Never delete the last copy.** Deletion operates per duplicate group
   and the app refuses to delete every member — at least one survivor per
   group is guaranteed at the data level, not just the UI.
2. **Recycle Bin, not permanent erase.** Files go to the OS Recycle Bin
   (via `send2trash` or the Win32 shell API), so any mistake is
   recoverable outside the app. No `os.remove`.
3. **Review before action, always.** A deletion manifest (CSV, same shape
   discipline as the rename manifest) is generated and shown for approval
   before anything moves. Dry-run is the default.
4. **Undo within the app.** A deletion log mirrors the rename undo log;
   "Undo Delete" restores from the Recycle Bin by recorded original path.
5. **Verify identity at delete time, not just index time.** Before trashing
   a file, re-confirm it still matches its group's SHA-256. If the file
   changed since indexing, skip it and report — never trust a stale hash to
   authorize a delete.

## In scope

### 1. Keep-policy selection per group
- Choose which copy survives each duplicate group by a policy the user
  picks: shortest path, oldest capture date, specific preferred root
  (e.g. "always keep the copy under `H:\Pictures\Pictures`"), or manual.
- Default policy proposed, user can override per group in review.

### 2. Deletion review + manifest
- A review view listing each group: survivor (highlighted) and the copies
  proposed for deletion, with sizes and the running reclaimable total.
- Export a deletion manifest CSV before acting; nothing is trashed until
  the user confirms from the review.

### 3. Safe delete execution
- Move-to-Recycle-Bin with a pre-delete SHA-256 re-check; per-file result
  (trashed / skipped-changed / error) recorded in a deletion undo log.
- Catalog rows for trashed files are marked (status `deleted`) or removed
  per a decision in design — not silently dropped.

### 4. Undo delete
- Restore trashed files from the Recycle Bin using the deletion log,
  mirroring the rename undo semantics (refuse to overwrite, handle missing).

## Out of scope

- Near-duplicate / perceptual-hash detection (this milestone is exact
  duplicates only — the ones already detected).
- Permanent deletion, emptying the Recycle Bin, or any bypass of it.
- Auto-deletion without review. Every delete is user-approved.
- Cloud, AI, or organization features (unchanged constraint).

## Acceptance criteria

- Deleting duplicates on a copy of the real archive reclaims space with
  every group retaining exactly its chosen survivor; a scripted check
  confirms no group is left empty.
- Every deleted file is recoverable from the Recycle Bin, and in-app Undo
  Delete restores them to original paths.
- A file modified after indexing is never trashed on a stale hash — it is
  skipped and reported.
- Existing suite stays green; new tests cover keep-policy selection,
  last-copy protection, pre-delete hash re-check, trash, and undo, using
  a temp-Recycle-Bin or injected trash function so tests touch no real bin.

## Verification approach

Same discipline as M1/M2: native Python 3.14 suite plus a scripted
end-to-end run on a *copy* of a real duplicate-heavy folder (never the
original), proving reclaimed space, survivor integrity, and full restore.
The trash operation is injected in tests so no file leaves the temp tree.

## Verification outcome

- Merged. The native suite (89 tests under `python -m pytest -q`) covers keep-policy selection, last-copy protection, pre-delete SHA-256 re-check, trash, undo with an injected provider, the delete-review UI flow, and controller validation that rejects forged reviews.
- The user confirmed the M3 real-world copy-folder verification: deletion reclaimed space with every group retaining its chosen survivor, and the verified survivor/deletion behavior held on a copy of a real duplicate-heavy folder.
- The Undo Delete action and `UndoDeleteService` pass tests that inject a trash provider returning a destination path; under that injected provider, trashed files are restored to their original paths. This is an injected-provider proof, not a default-production proof.
- Known blocker: the default production trash provider is `_send_to_trash` in `engine/delete/delete_service.py`, which calls the installed `send2trash.send2trash()`. That function returns `None`, so the deletion log records `trashed_to: null`, and `UndoDeleteService` refuses restoration when `trashed_to` is `None`. Default production Recycle Bin deletions are therefore **not currently reversible** through Undo Delete until a recoverable destination integration is implemented. This does not affect survivor retention, the pre-delete SHA-256 recheck, or the Recycle Bin (non-permanent) deletion guarantee.
- Honest caveats: deletion is exact-duplicate only (SHA-256); near-duplicate / perceptual-hash detection is not included. Deletion never permanently erases or empties the Recycle Bin; there is no permanent-delete path.
