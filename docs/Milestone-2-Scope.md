# Milestone 2 — Performance at Real-Library Scale

Date scoped: 2026-07-11
Status: proposed

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
