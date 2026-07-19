# Photo Curator Docs

Project notes and documentation live here. Files in this directory use standard Markdown and can be opened directly as an Obsidian vault folder.

## Notes

- [[Architecture]] — design goals, runtime flow, services, persistence, threading, safety boundaries, and duplicate-deletion architecture (including the Undo Delete known blocker).
- [[Milestone-2-Scope]] — performance at real-library scale. Status: merged; throughput benchmarked at 10k; full 10k UI acceptance not yet recorded.
- [[Milestone-3-Scope]] — reviewed duplicate deletion. Status: merged; user confirmed real-world copy-folder verification of survivor/deletion behavior; Undo Delete passes injected-provider tests but production restoration of default `send2trash` moves is a known blocker.
- [[Performance-Audit-2026-07-11]] — hotspot analysis tied to Milestone 2 scope.

## Current status

- [[../outputs/Photo-Curator-Status-2026-07-19|Photo-Curator-Status-2026-07-19]] is the current status note and supersedes [[../outputs/Photo-Curator-Handoff-2026-07-10|Photo-Curator-Handoff-2026-07-10]] for current status. The 2026-07-10 handoff is retained for history and is not rewritten.
