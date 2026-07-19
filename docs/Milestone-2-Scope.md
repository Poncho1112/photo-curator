# Milestone 2 — Performance at Real-Library Scale

Date scoped: 2026-07-11
Status: merged — throughput optimizations landed and are benchmarked at 10k; full 10k UI acceptance against a real archive is not yet recorded.

## Goal

Photo Curator v0.1 is correct and safe. Milestone 2 makes it fast and
comfortable against a real archive of 10,000+ photos, so it can be used to
curate the actual library rather than test folders.

## Why this milestone (and not features)

Every workflow in v0.1 — scan, browse, filter, rename, undo — is verified,
but only against small fixtures. The known hot spots below will surface the
first time a real archive is loaded. Fixing them changes no user-facing
behavior, so the tested engine architecture stays intact.

## In scope

### 1. Benchmark harness first
- Synthetic library generator (target: 10,000 mixed-size JPEGs in nested
  folders) plus timed runs of scan, index, re-scan, and thumbnail batch.
- Numbers recorded before any optimization so every change is measured,
  not guessed.

### 2. Scan and index throughput
- `PhotoRepository` commits per row (`insert`/`update` each call
  `connection.commit()`); indexing 10k photos means 10k+ transactions.
  Batch the index pass into one transaction.
- Incremental re-scan: skip SHA-256 hashing for files whose path, size,
  and mtime are unchanged. Hashing every file on every scan is the
  dominant cost and is pure waste on a stable library.
- `mark_missing_except` resolves every catalog path on every scan
  (`Path.resolve()` per record); make it set-based.

### 3. Thumbnail pipeline
- Visible-first priority: generate thumbnails for on-screen tiles before
  off-screen ones (currently request order is grid order).
- Cache size cap with simple eviction so the thumbnail folder cannot grow
  unbounded under app-data.

### 4. UI responsiveness at 10k records
- Grid currently creates one `PhotoTile` widget per visible record on
  every `populate()`; measure and, if needed, virtualize or lazily create
  tiles so filter changes stay under ~100 ms perceived.
- Status/folder counts already avoid repeated stat calls; verify that
  holds at 10k.

## Out of scope (unchanged from Milestone 1)

- AI tagging, OCR, face recognition, cloud services, automatic
  organization, React, Tauri, FastAPI.
- Rewriting the engine/database architecture.
- Any change to rename/undo safety semantics.

## Acceptance criteria

- Full first scan + index of the 10k synthetic library completes without
  UI freezes; progress and cancel remain functional throughout.
- Re-scan of an unchanged 10k library finishes in seconds, not minutes
  (no re-hashing of unchanged files).
- Filter/search changes on the 10k library render in under ~100 ms
  perceived (no visible stall).
- Window close during any long batch remains prompt (< 5 s), matching the
  Milestone 1 windowed close check.
- Existing test suite still passes; each optimization lands with a
  benchmark number in the PR description (before → after).

## Verification approach

Same discipline as Milestone 1: native Python 3.14 test suite, plus the
benchmark harness run before/after each optimization. End-to-end smoke
(scan → filter → rename → undo) re-run against the synthetic library
before the milestone is called done.

## Verification outcome

- Merged. The 10k synthetic-library benchmark artifacts under `benchmarks/` record first-index throughput improving from **31.96 s** (baseline, `baseline-2026-07-11-10k.json`) to **0.31 s** (`after-incremental-rescan-2026-07-11-10k.json`), and an unchanged re-scan improving from **103.83 s** to **3.84 s**.
- Landed: index batching into one transaction, incremental re-scan (skip SHA-256 when path/size/mtime are unchanged), set-based `mark_missing_except`, visible-first thumbnail priority, and the thumbnail cache cap.
- The native suite is green (89 tests under `python -m pytest -q`).
- An offscreen desktop smoke (scan → filter → rename + undo, close during thumbnails, filter during thumbnails) is green and is recorded as automated, not visual; in the final verifier, window close completed in 0.012 s and filter apply in 0.011 s. These map to the in-repo tests `test_close_cancels_and_joins_active_thumbnail_thread` and `test_active_thumbnail_batch_queues_refresh_for_latest_visible_records`.
- Honest caveat: full 10k UI acceptance against a real archive is not yet recorded in-repo. Throughput is benchmarked; the real-library UI run is not.
