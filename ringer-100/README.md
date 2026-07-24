# Ringer-100: a bounded Windows release-readiness loop for Photo Curator

This directory is a self-contained, bounded improvement loop that scores
Photo Curator's Windows release readiness against a fixed rubric, proposes
and host-verifies fixes, and stops on a validated 100 -- or on a clear,
documented blocker. It is intentionally bounded: **it cannot manufacture a
100 by loosening the rubric or the validators.** The fix worker never has
access to `rubric-v1.json`, `validate_score.py`, `validate_patch.py`, or
`verify_windows_undo.py` (`validate_patch.py` hard-rejects any patch that
touches them, independent of anything a worker claims), and every score is
independently re-derived by `validate_score.py` against the pinned,
hash-checked rubric -- not trusted from worker assertion. `host_gates` and
`host_evidence_paths` are always forcibly overwritten by the controller with
host-generated values before the final check; a worker cannot manufacture or
override them (see **Host authority** below).

Ringer itself has no native loop or dependency graph. `run_loop.py` **is**
the loop: each round it materializes ordinary, lintable Ringer manifests for
a review phase, an optional fix phase, host verification, and a regrade
phase (identical rubric/prompt to the review), running `ringer.py lint`
before every `ringer.py run ... --identity codex-orchestrator`, under one
reused `run_name` and one state root.

## This machine's host environment

Ringer only exists inside WSL on this machine (`/home/poncho/ringer`; there
is no `C:\Users\Poncho\ringer`). **`run_loop.py` therefore only runs inside
WSL.** Every normal / `--dry-run` / `--resume` invocation must go through
WSL:

```
wsl -e bash -lc 'cd /mnt/c/Users/Poncho/photo-curator && python3 ringer-100/run_loop.py'
wsl -e bash -lc 'cd /mnt/c/Users/Poncho/photo-curator && python3 ringer-100/run_loop.py --dry-run'
wsl -e bash -lc 'cd /mnt/c/Users/Poncho/photo-curator && python3 ringer-100/run_loop.py --resume'
```

If `run_loop.py` is started from a Windows-native Python instead, it
refuses to proceed -- **before any mutation** -- and prints this exact
command to re-run (see `require_wsl_host` in `run_loop.py`). This is a hard
gate, not a suggestion: under Windows-native Python, `running_under_wsl()`
is false and `default_ringer_root()` would otherwise resolve to the
nonexistent `C:\Users\Poncho\ringer`.

Host gates are the one place Windows-native tooling is still invoked, but
always *from inside WSL*: `run_host_verification` resolves the main
checkout's `.venv\Scripts\python.exe` once (`resolve_windows_venv_python`)
and calls it via WSL's Windows-interop, using its absolute WSL-visible path
-- never a Windows `C:\...` path in a command executed by WSL `python3`, and
never a relative path (the integration worktree that most host-verification
commands run inside never has the gitignored `.venv`; see **Host command
paths** below).

## Files

| File | Role |
|---|---|
| `manifest.json` | Immutable, versioned loop configuration: rubric path + pinned sha256, run name, round/plateau limits, worker selection, host verification commands. |
| `run_loop.py` | The orchestrator described above. |
| `rubric-v1.json` | The fixed scoring rubric (7 categories summing to 100, 3 hard caps, required host gates, the shared review/regrade prompt). |
| `validate_score.py` | Independently validates a score JSON against the rubric: hash/version, categories/maxima/total, finding evidence, host gates, discretionary overrides, and (at a claimed 100) host evidence -- including the state-root boundary and the windows_undo_verified structured evidence + three screenshots -- cross-platform messaging, and current docs/test counts. |
| `validate_patch.py` | Validates a fix worker's exported patch: rejects deletions, renames, path traversal/absolute paths, edits to protected validator/rubric/manifest files or `.git`/Ringer configuration, and any file outside the review's explicit `owned_files` allowlist. |
| `verify_windows_undo.py` | Host verifier: guards its own inputs (Windows only, isolated artifact root confined under a caller-specified state root, never a personal-media-looking path) before generating fixture JPEGs and driving the real `MainWindow` through Scan / Delete Duplicates / Undo Delete. Its import source (`--repo-root`, the integration worktree) and its isolation boundary (`--state-root`, always the main checkout's state root) are independent -- so it always exercises this round's actual fixes while the round directory it writes to can never end up inside a worktree that might later be discarded. |
| `tests/test_loop.py` | Unit tests for all of the above, using fake subprocess runners -- no real Ringer run, model call, or Recycle Bin touch. |

## The corrected lifecycle

Every phase of every round targets one **controller-created, detached
integration worktree** under the state root -- never the user's main
checkout mid-loop:

1. **On the first `run_loop.py` invocation of a run** (not `--resume`ing an
   existing one), after the clean-tree gate passes, the controller resolves
   the main checkout's current `HEAD` and creates a detached git worktree at
   `ringer-100/state/integration-worktree` (`create_integration_worktree`).
   This worktree, and its base commit, are persisted in `state.json` so a
   `--resume` reuses the *same* worktree rather than recreating it.
2. **Review**: a read-only Claude worker is granted read access to the
   integration worktree via `--add-dir` (see **Worker access** below) and
   writes its findings to its own per-task artifact path, `./score-worker.json`,
   inside the working directory Ringer actually creates for the task --
   `round-N/review/review-round-NN/score-worker.json` (Ringer always creates
   a task's cwd at `<manifest workdir>/<task key>`, one level below the
   phase's own `workdir`; the worker is never told, and never granted, write
   access to the phase directory itself). The controller reads that exact
   path and copies it into the round's canonical `round-N/review/score.json`,
   then validates it.
3. **Fix** (only if the review has confirmed P0/P1/P2 findings): before the
   fix worker runs, the controller snapshots the integration worktree's exact
   current state as a git tree object (`round-N/fix/round-trees.json`'s
   `pre_tree`) via a round-scoped **alternate index** -- a throwaway
   `GIT_INDEX_FILE` (never the worktree's real index) seeded from `HEAD`
   (`git read-tree HEAD`), staged with the current worktree contents
   (`git add -A`, so new/untracked files are captured too), and turned into a
   tree object with `git write-tree`. The fix worker edits the integration
   worktree *directly* (via `--add-dir`, not a separate Ringer worktree --
   see **Manifest feasibility** below) and never commits. After the worker
   returns, the controller takes a second alternate-index snapshot
   (`post_tree`) and isolates exactly this round's new delta with
   `git diff --binary <pre_tree> <post_tree>` (`compute_round_delta`), then
   validates *only that delta* against this round's `owned_files` allowlist.
   Diffing two tree objects, rather than reverse-applying a stored patch,
   means a later round editing lines an earlier round already touched can
   never fail to isolate -- there is no patch to reverse-apply against the
   worktree, so overlapping hunks never conflict. Each alternate index file
   is deleted (`Path.unlink` on the exact file, never a directory wipe) as
   soon as its tree ID has been captured.
4. **Host verification** runs against the integration worktree (native test
   suite, `compileall`, `pip check`, targeted tests, and the Windows UI
   undo/redo verifier), writing `round-N/host-gates.json` and
   `round-N/host-evidence.json`.
5. If verification fails, the controller restores the exact pre-round
   integration state with `git apply -R` against the worktree (never
   `git reset`, `git checkout`, `git clean`, or the real index), then
   verifies the rollback by recomputing a fresh alternate-index tree ID for
   the post-rollback worktree and comparing it against the round's stored
   `pre_tree` ID (`rollback_round_delta`) -- not by comparing diff text.
6. **Regrade** uses the *identical* rubric/prompt as review. The worker
   writes `./score-worker.json` in its own Ringer-created task directory
   (`round-N/regrade/regrade-round-NN/score-worker.json`); the controller
   reads that exact path, then builds the canonical `round-N/regrade/score.json`
   by copying the worker's score and **forcibly replacing `host_gates` and
   `host_evidence_paths` with the host-generated values from step 4** (see
   **Host authority** below), then validates that.
7. If the regrade fails validation, the controller rolls back this round's
   delta exactly as in step 5 and continues to the next round.
8. If the regrade validates at a total of **100**, the controller exports
   the full cumulative binary diff from the integration worktree
   (`ringer-100/state/final-cumulative.patch`), validates it against every
   `owned_files` allowlist declared this run, confirms the main checkout is
   still clean, and applies it **once** (`git apply --check` then `git
   apply`) -- leaving the main checkout uncommitted for human/Codex review.
9. On any block (safety error, plateau, max rounds), **both the integration
   worktree and the main checkout are left exactly as they were** -- the
   main checkout is never touched outside step 8.

## Worker access and output

Claude workers run sandboxed to their task's working directory; without
explicit access, a review/regrade worker whose `cwd` is a per-task artifact
directory under `ringer-100/state/` cannot read the rest of the repository.
Every review/fix/regrade task spec therefore states the integration
worktree's path explicitly and grants access to it via a Claude
`--add-dir` engine arg, computed by a pure WSL-to-Windows path converter
(`wsl_mnt_path_to_windows`): a WSL `/mnt/<drive>/...` path becomes a
Windows-native `<drive>:\...` path (Claude's `--add-dir` expects a native
Windows path even though the worker itself runs inside WSL). Paths that
cannot be represented this way (i.e. not rooted under `/mnt/<drive>`) are
rejected outright rather than silently guessed. Note that `--add-dir` grants
access only to the integration worktree, never to the phase directory or
the main repository -- the worker's write access to its own `score-worker.json`
comes entirely from the task cwd Ringer itself creates, not from any grant
this loop adds.

Ringer creates every task's actual working directory at `<manifest
workdir>/<task key>` -- one level *below* the `workdir` a phase's manifest
declares. Review/regrade workers therefore write their score to `./score-worker.json`,
relative to that task cwd (i.e. `round-N/review/review-round-NN/score-worker.json`
or `round-N/regrade/regrade-round-NN/score-worker.json`), and `expect_files`
names it the same way -- `score-worker.json`, not an absolute path -- since
Ringer's own check step also runs with that task directory as its cwd. This
is never the round's canonical `score.json`, and never the phase directory
itself (`round-N/review/score-worker.json` would be the task cwd's *parent*,
which the worker has no authorized write access to -- an earlier version of
this loop asked for exactly that path and relied on unauthorized parent-
directory writes to work). **The controller, not the worker, reads the
worker's own artifact path and copies/normalizes it into the round's
canonical score.json** (see `review_worker_score_path` /
`regrade_worker_score_path` in `run_loop.py`), so the authoritative score
file is never something a worker path-traversed its way into writing
directly.

## Host authority

A worker's `host_gates` and `host_evidence_paths` fields are informational
only -- during regrade, the worker is shown the round's actual host gate
results (already known by regrade time, since host verification runs
*before* regrade) for reference, but cannot override them. After Ringer
returns the worker's `score-worker.json`, the controller **always**
overwrites `host_gates` with the real `gate_results` dict and
`host_evidence_paths` with the controller-computed evidence paths before
writing the canonical `score.json` and validating it. At a claimed 100,
`validate_score.py` independently verifies every evidence path: it must
exist on disk, and it must resolve inside the configured state root
(never outside it, and never inside the integration worktree, which can be
discarded); for `windows_undo_verified` specifically, it additionally
requires the structured `evidence.json` (with `status == "passed"`) plus
all three screenshots (`delete-review.png`, `after-delete.png`,
`after-undo.png`), each nonempty.

## Manifest feasibility

Materialized review/regrade manifests declare an accessible repo (the
integration worktree, via `--add-dir`) and a task-relative expected
deliverable (`score-worker.json`, checked from the task's own cwd -- see
**Worker access and output** above). The fix phase edits the *same* dedicated
integration worktree directly -- it does **not** spawn a second, nested
Ringer worktree (no `worktrees: true` on the fix manifest); Ringer's own
worktree isolation would give the fix task a fresh clone of `HEAD` every
round, which is exactly the accumulation bug this design fixes. Every check
in every phase prints why it fails rather than a bare pass/fail.

`validate_manifest_shape` and `materialize_round_manifests_for_lint` are a
host-only structural lint over materialized manifests -- no subprocess, no
model call, and no invocation of `ringer.py lint` itself (this loop never
spawns another orchestration layer to check its own output). They exist so
the manifests stay honest without requiring a live Ringer run to catch a
missing field. `ringer-100/manifest.json` is not one of these -- it is the
loop's own immutable configuration, not a materialized Ringer manifest.

## Host command paths

A detached integration worktree never has the gitignored `.venv` (a fresh
`git worktree add` only checks out tracked files). Every host verification
command therefore uses the main checkout's Windows venv interpreter,
resolved once by absolute path (`resolve_windows_venv_python`), while
running with `cwd` set to the integration worktree -- so native suite,
`compileall`, `pip check`, targeted tests, and `verify_windows_undo.py` all
exercise this round's actual accumulated fixes, not the pristine main
checkout. `ringer-100/*.py` itself (this loop's own orchestration tooling)
is likewise never assumed to exist inside the worktree -- every reference
to it (e.g. `verify_windows_undo.py`) resolves to an absolute path in the
main checkout, never a worktree-relative one.

The Windows UI verifier receives two independent roots: `--repo-root` (the
integration worktree, for `sys.path` / imports, so it exercises this
round's real code) and `--state-root` (always the main checkout's state
root, the isolation boundary its round directory must be confined under).
These are deliberately decoupled so the isolation boundary never drifts
into a worktree that a later round's rollback could alter or that a
completed run's cleanup could remove.

## Commands

Lint your own understanding of the code (byte-compile + tests), from the
main repo root (Windows-native Python -- this is one of the few commands
that legitimately runs outside WSL, since it only exercises this loop's own
unit tests with fake subprocess runners, never a real Ringer run):

```
.venv/Scripts/python.exe -m pytest ringer-100/tests -q
.venv/Scripts/python.exe -m compileall -q ringer-100
git diff --check -- ringer-100
python3 -m json.tool ringer-100/manifest.json
python3 -m json.tool ringer-100/rubric-v1.json
```

Everything below runs **inside WSL** (see **This machine's host
environment** above); running these from Windows-native Python fails before
any mutation with the exact WSL command to use instead.

Print the next round's plan with no mutation, no subprocess calls, and no
model calls:

```
wsl -e bash -lc 'cd /mnt/c/Users/Poncho/photo-curator && python3 ringer-100/run_loop.py --dry-run'
```

Start a fresh bounded run (refuses unless `git status` is clean outside the
state root; on the first round, also creates the integration worktree):

```
wsl -e bash -lc 'cd /mnt/c/Users/Poncho/photo-curator && python3 ringer-100/run_loop.py'
```

Resume a previously started run from its persisted state (`state.json`),
reusing the same integration worktree:

```
wsl -e bash -lc 'cd /mnt/c/Users/Poncho/photo-curator && python3 ringer-100/run_loop.py --resume'
```

Override any of the resolved paths explicitly (still inside WSL; use WSL
`/mnt/...` paths, not Windows `C:\...` paths):

```
wsl -e bash -lc 'cd /mnt/c/Users/Poncho/photo-curator && python3 ringer-100/run_loop.py \
  --repo /mnt/c/path/to/photo-curator --ringer-root /home/poncho/ringer \
  --state-root /mnt/c/path/to/state --resume'
```

Inspect a round's artifacts: everything lives under one state root
(`ringer-100/state/` by default, git-ignored via `ringer-100/.gitignore`,
which also covers the integration worktree since it lives at
`ringer-100/state/integration-worktree/`). Under that root:
`integration-worktree/` (the persistent worktree every phase targets),
`final-cumulative.patch` (written on a validated 100, before it is applied
to the main checkout), one subdirectory per round (`round-01/`,
`round-02/`, ...) each holding the materialized `review-manifest.json` /
`fix-manifest.json` / `regrade-manifest.json`, their `review/score.json` /
`fix/round-delta.patch` / `fix/round-trees.json` (the round's `pre_tree` /
`post_tree` alternate-index tree IDs) / `regrade/score.json`,
`host-gates.json`, `host-evidence.json`, and (if the round's fix was rolled
back or the loop stopped) `BLOCKED.md` at the state root.

**Prerequisite:** commit `ringer-100/` itself to git before running for
real. The clean-tree gate (`assert_clean_repo`) requires `git status` to be
clean outside the state root, and an uncommitted `ringer-100/` shows up as
untracked there.

Manual stop: there is no separate "stop" command -- interrupt the process
(it never leaves the integration worktree in a state that isn't either a
kept, validated round or a confirmed rollback: see **Integration safety**
below), or simply do not invoke `--resume` again. Re-running without
`--resume` after a prior run exists raises an error rather than silently
restarting.

## Recovery and cleanup

- **Resuming after an interruption**: `--resume` picks up from the last
  saved `state.json`, reusing the existing integration worktree. The
  worktree is always either mid-round-in-progress (if the process died
  mid-round, inspect `round-N/fix/round-delta.patch` and `host-gates.json`
  to see how far it got) or in a clean, fully-rolled-back-or-kept state
  between rounds -- never partially applied.
- **Abandoning a run**: the integration worktree is a real git worktree
  registered against the main checkout. To discard it entirely, run (from
  the main checkout, not from inside the worktree) `git worktree remove
  ringer-100/state/integration-worktree` (add `--force` only if you have
  confirmed you want to discard uncommitted changes in it), then delete
  `ringer-100/state/state.json` so a future run starts fresh rather than
  trying to resume. This is a manual, human-reviewed step -- `run_loop.py`
  never removes the worktree itself.
- **After a validated success**: the main checkout has one uncommitted
  `git apply` of `ringer-100/state/final-cumulative.patch` for human/Codex
  review. The integration worktree is left in place (for audit) until
  manually removed as above.
- **After a BLOCKED run**: read `ringer-100/state/BLOCKED.md` for the
  reason, score history, last confirmed deductions, and a safe next action.
  The main checkout is untouched; the integration worktree holds exactly
  the last kept (non-rolled-back) round's state.

## Safety

- **Ownership.** The fix worker's Ringer task spec names its exact
  `owned_files` allowlist (supplied by the review) and explicitly forbids
  `ringer-100/rubric-v1.json`, `ringer-100/validate_score.py`,
  `ringer-100/validate_patch.py`, `ringer-100/verify_windows_undo.py`,
  `ringer-100/manifest.json`, any `.git` path, and any test file not
  explicitly declared. `validate_patch.py` enforces this independent of the
  spec text: it rejects deletions, renames, absolute paths, path traversal,
  and any changed path outside the allowlist or inside the always-protected
  set, before a patch is ever kept or exported.
- **Integration safety.** Before each round's fix, the controller snapshots
  the integration worktree's exact current state as a git tree object
  (`pre_tree`) via a round-scoped alternate index -- never the worktree's
  real index. After the fix worker returns, it takes a second snapshot
  (`post_tree`) and isolates and validates only this round's newly
  introduced delta (`git diff --binary <pre_tree> <post_tree>`, never the
  full accumulated diff) against that round's `owned_files`. Because this
  diffs two tree objects rather than reverse-applying a stored patch against
  the worktree, a later round editing lines an earlier round already
  touched can never fail to isolate. If host verification or the regrade
  fails, it restores the exact pre-round integration state with `git apply
  -R` against the worktree (never `git reset`, `git checkout`, `git clean`,
  or the real index), then confirms the rollback by recomputing a fresh
  alternate-index tree ID and comparing it against the stored `pre_tree`
  ID. On a validated 100, the full cumulative patch is exported,
  re-validated against every `owned_files` allowlist declared this run,
  and applied exactly once (`git apply --check` then `git apply`) to the
  still-clean main checkout. On any block, the main checkout is never
  touched and the integration worktree is preserved exactly as it was.
- **Clean-tree gate.** A fresh (non-`--resume`) run refuses to start unless
  `git status` is clean outside the configured state root.
- **Windows verifier guards.** `verify_windows_undo.py` refuses to run on a
  non-Windows platform, refuses any round directory not confined under its
  caller-specified state root (always the main checkout's, never a
  worktree's), and refuses any path whose resolved form contains a
  personal-media-looking path segment (Pictures, Photos, Desktop, Documents,
  OneDrive, DCIM, Downloads, Camera Roll). It only ever touches JPEGs it
  generates itself inside that isolated directory, and it drives the
  production UI only through the same `MainWindow` QActions and dialogs a
  human uses -- it never calls `DeleteService`/`UndoDeleteService` directly.

## Why this can't cheat its way to 100

- The rubric and every validator are outside the fix worker's reach (see
  Ownership above), and `validate_score.py` recomputes the rubric's sha256
  from the file on disk every time -- it does not trust a manifest-declared
  hash it can't verify.
- A total of 100 requires, simultaneously: every category at its stated
  maximum, every rubric-declared host gate `true` (forced by the
  controller, never a worker's claim -- see Host authority above), zero
  P0/P1/P2 findings, `host_evidence_paths` pointing at host-generated
  verification that independently, on every validation, is confirmed to
  exist on disk inside the configured state root (and, for
  `windows_undo_verified`, a structured evidence.json reporting
  `status == "passed"` plus three nonempty screenshots), declared runtime
  dependencies, `cross_platform_destructive_actions` set to `disabled` or
  `unsupported_accurate` (never a false claim that non-Windows destructive
  actions are recoverable), and `docs_test_count` that matches what
  `validate_score.py` independently recounts in the current repository --
  not a remembered or asserted number.
- `discretionary_override` is rejected unconditionally, at any score.
- This automation stops on a **validated** 100 only: `validate_score.py`,
  not the worker, is the gate -- and the main checkout is touched exactly
  once, after that validation, never before.
