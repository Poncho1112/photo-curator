"""Bounded Ringer improvement loop for Photo Curator's Windows release readiness.

Ringer has no native loop or dependency graph, so this script IS the loop:
it materializes an ordinary, lintable Ringer manifest for each phase of each
round (review -> fix -> host verification -> regrade), invokes ``ringer.py
lint`` before every ``ringer.py run ... --identity codex-orchestrator``,
reuses one ``run_name`` across all rounds, parses the machine-readable score
JSON each phase produces, and persists every round under one state root.

This machine's Ringer install only exists inside WSL (``/home/poncho/ringer``);
this script therefore refuses to run under a Windows-native Python (see
``require_wsl_host``) and fails before any mutation with the exact WSL
command to use instead.

All review, fix, host-verification, and regrade phases target one
controller-created, detached **integration worktree** under the state root
(see ``create_integration_worktree``), never the user's main checkout, so
fixes accumulate across rounds. The main checkout is touched at most once:
a single ``git apply`` of the full cumulative, validated patch on a
validated 100, left uncommitted for human/Codex review. On any block, both
the integration worktree and the main checkout are left exactly as they
were.

This is intentionally bounded: it stops on a validated 100, after 10 rounds,
after two consecutive non-improving rounds, or on any integration-safety
failure -- and it can never manufacture a 100 by loosening rubric-v1.json or
the validators, because the fix worker is never given access to them (see
``validate_patch.py``) and every score is independently re-checked by
``validate_score.py`` against the immutable, hash-pinned rubric.

Usage (inside WSL):
    python3 ringer-100/run_loop.py --dry-run
    python3 ringer-100/run_loop.py
    python3 ringer-100/run_loop.py --resume
See README.md for the full command reference.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import validate_patch  # noqa: E402
import validate_score  # noqa: E402

RunFunc = Callable[..., subprocess.CompletedProcess]

RINGER_100_DIR = Path(__file__).resolve().parent
REPO_ROOT_FROM_HERE = RINGER_100_DIR.parent


class LoopError(RuntimeError):
    """A safety, integration, or configuration failure that stops the loop."""


# --------------------------------------------------------------------------
# Environment defaults
# --------------------------------------------------------------------------


def running_under_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False


def default_repo_root() -> Path:
    if running_under_wsl():
        return Path("/mnt/c/Users/Poncho/photo-curator")
    return REPO_ROOT_FROM_HERE


def default_ringer_root() -> Path:
    if running_under_wsl():
        return Path("/home/poncho/ringer")
    return Path.home() / "ringer"


def default_state_root(repo_root: Path, config: dict) -> Path:
    return repo_root / config["state_root"]


def wsl_invocation_command(argv: list[str]) -> str:
    """The exact command a Windows-native invocation should be replaced with."""
    inner = "cd /mnt/c/Users/Poncho/photo-curator && python3 ringer-100/run_loop.py"
    if argv:
        inner += " " + " ".join(argv)
    return f"wsl -e bash -lc '{inner}'"


def require_wsl_host(argv: list[str]) -> None:
    """Refuse to proceed unless running inside WSL, before any mutation.

    This machine's Ringer install only exists at /home/poncho/ringer (inside WSL). Starting
    run_loop.py from a Windows-native Python leaves running_under_wsl() False, which used to
    silently resolve default_ringer_root() to the nonexistent C:\\Users\\Poncho\\ringer. Now every
    normal/--dry-run/--resume invocation must go through WSL, or this raises before any file is
    touched, printing the exact command to re-run.
    """
    if running_under_wsl():
        return
    raise LoopError(
        "run_loop.py must run inside WSL, not a Windows-native Python -- this machine's Ringer "
        "install only exists at /home/poncho/ringer, unreachable from Windows-native Python. "
        f"Re-run as:\n  {wsl_invocation_command(argv)}"
    )


# --------------------------------------------------------------------------
# WSL <-> Windows-native path conversion (Claude --add-dir, and any absolute
# WSL path handed to a Windows-native host-gate command)
# --------------------------------------------------------------------------

_WSL_MNT_RE = re.compile(r"^/mnt/([A-Za-z])(/.*)?$")


def wsl_mnt_path_to_windows(path: Path | str) -> str:
    """Pure converter: a WSL ``/mnt/<drive>/...`` path -> a Windows-native ``<drive>:\\...`` path.

    Used both for the Claude worker's ``--add-dir`` engine arg and, in ``run_host_verification``,
    for every absolute WSL path handed to the Windows-native venv Python -- WSL interop only
    translates the *executable's* own path, never its arguments, so a raw ``/mnt/c/...`` argument
    gets misread by Windows as rooted at the current drive (``C:\\mnt\\c\\...``) instead of failing
    loudly.

    Raises ``LoopError`` if ``path`` cannot be represented as a native Windows path (i.e. it is
    not rooted under ``/mnt/<single-drive-letter>``) -- never silently guesses.
    """
    posix = Path(path).as_posix()
    match = _WSL_MNT_RE.match(posix)
    if not match:
        raise LoopError(
            f"cannot convert {str(path)!r} to a Windows-native path "
            "(expected a WSL '/mnt/<drive>/...' path)"
        )
    drive = match.group(1).upper()
    rest = (match.group(2) or "").lstrip("/")
    windows_rest = rest.replace("/", "\\")
    return f"{drive}:\\{windows_rest}" if windows_rest else f"{drive}:\\"


def add_dir_engine_args(native_windows_path: str) -> list[str]:
    return [f"--add-dir={native_windows_path}"]


# --------------------------------------------------------------------------
# Manifest (loop config) loading + rubric immutability
# --------------------------------------------------------------------------


def load_loop_manifest(manifest_path: Path) -> dict:
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LoopError(f"could not read loop manifest at {manifest_path}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise LoopError(f"loop manifest at {manifest_path} is not valid JSON: {exc}") from None


def assert_rubric_immutable(config: dict, repo_root: Path) -> None:
    rubric_path = repo_root / config["rubric_path"]
    if not rubric_path.is_file():
        raise LoopError(f"configured rubric_path does not exist: {rubric_path}")
    actual_hash = validate_score._sha256_of(rubric_path)
    expected_hash = config.get("rubric_sha256")
    if actual_hash != expected_hash:
        raise LoopError(
            f"rubric hash mismatch: manifest.json declares {expected_hash!r}, "
            f"{rubric_path} actually hashes to {actual_hash!r}. The rubric must never change "
            "underneath a running loop -- update manifest.json's rubric_sha256 deliberately "
            "and only as a human-reviewed, out-of-band change."
        )


# --------------------------------------------------------------------------
# Git safety helpers (subprocess argument arrays only, never shell text)
# --------------------------------------------------------------------------


def run_subprocess(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Thin wrapper so tests can monkeypatch a single call site."""
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return subprocess.run(cmd, **kwargs)  # noqa: S603 - argument arrays only, never shell=True


def git_status_porcelain(repo_root: Path, *, run: RunFunc = run_subprocess) -> list[str]:
    proc = run(["git", "-C", str(repo_root), "status", "--porcelain"])
    if proc.returncode != 0:
        raise LoopError(f"git status failed: {proc.stderr}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _porcelain_path(line: str) -> str:
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path.strip('"')


def assert_clean_repo(repo_root: Path, state_root: Path, *, run: RunFunc = run_subprocess) -> None:
    """Refuse to start unless git status is clean, ignoring only the state root."""
    lines = git_status_porcelain(repo_root, run=run)
    try:
        state_rel = state_root.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        state_rel = None

    dirty = []
    for line in lines:
        path = _porcelain_path(line)
        if state_rel and (path == state_rel or path.startswith(state_rel + "/")):
            continue
        dirty.append(path)

    if dirty:
        raise LoopError(
            "refusing to start: git status is not clean outside the state root "
            f"({state_rel or state_root}): " + ", ".join(dirty)
        )


def apply_patch(repo_root: Path, patch_path: Path, *, run: RunFunc = run_subprocess) -> None:
    check = run(["git", "-C", str(repo_root), "apply", "--check", str(patch_path)])
    if check.returncode != 0:
        raise LoopError(f"git apply --check failed for {patch_path}: {check.stderr}")
    applied = run(["git", "-C", str(repo_root), "apply", str(patch_path)])
    if applied.returncode != 0:
        raise LoopError(f"git apply failed for {patch_path}: {applied.stderr}")


def confirm_patch_reversible(repo_root: Path, patch_path: Path, *, run: RunFunc = run_subprocess) -> bool:
    proc = run(["git", "-C", str(repo_root), "apply", "--check", "-R", str(patch_path)])
    return proc.returncode == 0


def rollback_patch(
    repo_root: Path,
    patch_path: Path,
    pre_apply_snapshot: list[str],
    *,
    run: RunFunc = run_subprocess,
) -> None:
    reversed_apply = run(["git", "-C", str(repo_root), "apply", "-R", str(patch_path)])
    if reversed_apply.returncode != 0:
        raise LoopError(
            f"rollback failed: 'git apply -R {patch_path}' did not succeed: {reversed_apply.stderr}. "
            "Stopping immediately -- do not attempt git reset/checkout/clean."
        )
    post_snapshot = git_status_porcelain(repo_root, run=run)
    if post_snapshot != pre_apply_snapshot:
        raise LoopError(
            "rollback did not restore the exact pre-iteration tracked diff. "
            f"expected={pre_apply_snapshot!r} actual={post_snapshot!r}"
        )


# --------------------------------------------------------------------------
# Integration worktree lifecycle (accumulates fixes across rounds)
# --------------------------------------------------------------------------


def integration_worktree_dir(state_root: Path) -> Path:
    return state_root / "integration-worktree"


def create_integration_worktree(repo_root: Path, state_root: Path, *, run: RunFunc = run_subprocess) -> tuple[Path, str]:
    """Create, once, a detached git worktree of ``repo_root``'s current (clean) HEAD under the
    state root. Every phase of every round targets this worktree so fixes accumulate without
    ever touching the user's main checkout mid-loop."""
    worktree_dir = integration_worktree_dir(state_root)
    if worktree_dir.exists():
        raise LoopError(
            f"integration worktree already exists at {worktree_dir}; refusing to recreate it "
            "(pass --resume to reuse the existing run, or remove stale state manually first)"
        )
    head = run(["git", "-C", str(repo_root), "rev-parse", "HEAD"])
    if head.returncode != 0:
        raise LoopError(f"could not resolve HEAD to create the integration worktree: {head.stderr}")
    base_commit = head.stdout.strip()
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    proc = run(["git", "-C", str(repo_root), "worktree", "add", "--detach", str(worktree_dir), base_commit])
    if proc.returncode != 0:
        raise LoopError(f"failed to create integration worktree at {worktree_dir}: {proc.stderr}")
    return worktree_dir, base_commit


def snapshot_worktree_diff(integration_worktree: Path, *, run: RunFunc = run_subprocess) -> str:
    proc = run(["git", "-C", str(integration_worktree), "diff", "--binary"])
    if proc.returncode != 0:
        raise LoopError(f"git diff --binary failed for integration worktree {integration_worktree}: {proc.stderr}")
    return proc.stdout


def alternate_index_path(round_scoped_dir: Path, tag: str) -> Path:
    return round_scoped_dir / f".alt-index-{tag}"


def _cleanup_alternate_index_files(alt_index: Path) -> None:
    """Delete only the explicit temporary alternate-index file (and its git ``.lock`` sibling,
    if a failed git command left one behind) -- ``Path.unlink`` on exact files, never a
    recursive directory delete."""
    lock_path = alt_index.with_name(alt_index.name + ".lock")
    for path in (alt_index, lock_path):
        if path.exists():
            path.unlink()


def snapshot_worktree_tree(
    integration_worktree: Path,
    round_scoped_dir: Path,
    tag: str,
    *,
    run: RunFunc = run_subprocess,
) -> str:
    """Compute a git tree object ID for the exact current worktree state -- including new,
    untracked files -- via a round-scoped alternate index, and never the integration worktree's
    real index.

    A fresh, throwaway index file (``GIT_INDEX_FILE``, passed as a subprocess ``env`` mapping,
    never shell-interpolated) is seeded from ``HEAD`` (``git read-tree HEAD``), then the current
    worktree contents are staged into *that* index only (``git add -A``), and ``git write-tree``
    turns it into a tree object. The alternate index file is deleted (``Path.unlink``, not a
    directory wipe) as soon as the tree ID has been captured, whether or not this succeeds.
    """
    round_scoped_dir.mkdir(parents=True, exist_ok=True)
    alt_index = alternate_index_path(round_scoped_dir, tag)
    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = str(alt_index)
    try:
        read_tree = run(["git", "-C", str(integration_worktree), "read-tree", "HEAD"], env=env)
        if read_tree.returncode != 0:
            raise LoopError(f"failed to seed the {tag!r} alternate index from HEAD: {read_tree.stderr}")
        add_all = run(["git", "-C", str(integration_worktree), "add", "-A"], env=env)
        if add_all.returncode != 0:
            raise LoopError(
                f"failed to stage the current worktree into the {tag!r} alternate index: {add_all.stderr}"
            )
        write_tree = run(["git", "-C", str(integration_worktree), "write-tree"], env=env)
        if write_tree.returncode != 0:
            raise LoopError(f"failed to write a tree object for the {tag!r} snapshot: {write_tree.stderr}")
        return write_tree.stdout.strip()
    finally:
        _cleanup_alternate_index_files(alt_index)


def compute_round_delta(
    integration_worktree: Path,
    round_scoped_dir: Path,
    pre_tree: str,
    *,
    run: RunFunc = run_subprocess,
) -> tuple[str, str]:
    """Isolate exactly this round's newly introduced changes as a tree-to-tree diff.

    Snapshots the current worktree into a fresh alternate-index tree (``post_tree``) and diffs it
    against the round's already-captured ``pre_tree`` with ``git diff --binary <pre> <post>``.
    Unlike reverse-applying a stored patch, this is immune to a later round editing lines an
    earlier round already touched -- there is no patch to reverse-apply, so overlapping hunks
    never conflict. Returns ``(round_delta_patch_text, post_tree)``.
    """
    post_tree = snapshot_worktree_tree(integration_worktree, round_scoped_dir, "post", run=run)
    diff = run(["git", "-C", str(integration_worktree), "diff", "--binary", pre_tree, post_tree])
    if diff.returncode != 0:
        raise LoopError(f"git diff --binary {pre_tree} {post_tree} failed: {diff.stderr}")
    return diff.stdout, post_tree


def confirm_round_delta_reversible(
    integration_worktree: Path, round_delta_path: Path, *, run: RunFunc = run_subprocess
) -> bool:
    proc = run(["git", "-C", str(integration_worktree), "apply", "--check", "-R", str(round_delta_path)])
    return proc.returncode == 0


def rollback_round_delta(
    integration_worktree: Path,
    round_scoped_dir: Path,
    round_delta_path: Path,
    expected_pre_tree: str,
    *,
    run: RunFunc = run_subprocess,
) -> None:
    """Restore the exact pre-round integration worktree state, reversing only this round's
    isolated delta -- using only ``git apply -R`` against the worktree (never
    reset/checkout/clean, and never touching the real index: ``git apply`` without ``--index``
    only ever rewrites working-tree files). Verified by recomputing a fresh alternate-index tree
    ID for the post-rollback worktree and comparing it against the ``pre_tree`` ID captured
    before the fix ran -- not by comparing diff text."""
    reversed_apply = run(["git", "-C", str(integration_worktree), "apply", "-R", str(round_delta_path)])
    if reversed_apply.returncode != 0:
        raise LoopError(
            f"rollback failed: 'git apply -R {round_delta_path}' did not succeed against the "
            f"integration worktree: {reversed_apply.stderr}. Stopping immediately -- do not "
            "attempt git reset/checkout/clean."
        )
    post_rollback_tree = snapshot_worktree_tree(integration_worktree, round_scoped_dir, "rollback-verify", run=run)
    if post_rollback_tree != expected_pre_tree:
        raise LoopError(
            "rollback did not restore the exact pre-round integration worktree tree "
            f"(expected_tree={expected_pre_tree!r} actual_tree={post_rollback_tree!r})"
        )


# --------------------------------------------------------------------------
# Host command paths (the integration worktree never has the gitignored .venv)
# --------------------------------------------------------------------------


def resolve_windows_venv_python(main_repo_root: Path) -> Path:
    """Resolve the main checkout's Windows venv interpreter once, by absolute WSL path.

    A detached integration worktree never has the gitignored ``.venv``, so every host
    verification command must use this interpreter (resolved from the main checkout) while
    running with ``cwd`` set to the integration worktree."""
    venv_python = (main_repo_root / ".venv" / "Scripts" / "python.exe").resolve()
    if not venv_python.is_file():
        raise LoopError(
            f"Windows venv interpreter not found at {venv_python}. The main checkout must have "
            "its .venv set up before host verification can run (a detached integration worktree "
            "never has the gitignored .venv)."
        )
    return venv_python


# --------------------------------------------------------------------------
# Patch ownership validation (wraps validate_patch.py)
# --------------------------------------------------------------------------


def validate_and_load_allowlist(review_score: dict) -> tuple[list[str], list[str]]:
    owned_files = review_score.get("owned_files")
    if not isinstance(owned_files, list) or not owned_files:
        raise LoopError("review score JSON has no non-empty owned_files allowlist; refusing to run a fix phase")
    declared_fix_tests = review_score.get("declared_fix_tests", [])
    return owned_files, declared_fix_tests


def validate_fix_patch(patch_path: Path, owned_files: list[str], declared_fix_tests: list[str]) -> None:
    patch_text = patch_path.read_text(encoding="utf-8")
    result = validate_patch.validate_patch(patch_text, owned_files, declared_fix_tests=declared_fix_tests)
    if not result.ok:
        raise LoopError("fix patch rejected by validate_patch: " + "; ".join(result.failures))


# --------------------------------------------------------------------------
# Score validation (wraps validate_score.py)
# --------------------------------------------------------------------------


def validate_and_load_score(
    rubric_path: Path,
    score_path: Path,
    *,
    repo_root: Path,
    state_root: Path | None = None,
    check_current_counts: bool,
) -> tuple[dict, validate_score.ValidationResult]:
    if not score_path.is_file():
        raise LoopError(f"expected score JSON was not produced: {score_path}")
    score = json.loads(score_path.read_text(encoding="utf-8"))
    result = validate_score.validate(
        rubric_path,
        score_path,
        repo_root=repo_root,
        state_root=state_root,
        check_current_counts=check_current_counts,
    )
    return score, result


# --------------------------------------------------------------------------
# Atomic state persistence
# --------------------------------------------------------------------------


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def load_state(state_root: Path) -> dict | None:
    state_path = state_root / "state.json"
    if not state_path.is_file():
        return None
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(state_root: Path, state: dict) -> None:
    atomic_write_json(state_root / "state.json", state)


def round_dir(state_root: Path, round_no: int) -> Path:
    return state_root / f"round-{round_no:02d}"


# --------------------------------------------------------------------------
# Ringer task layout: <manifest workdir>/<task key> (never the workdir itself)
# --------------------------------------------------------------------------


def review_task_key(round_no: int) -> str:
    return f"review-round-{round_no:02d}"


def fix_task_key(round_no: int) -> str:
    return f"fix-round-{round_no:02d}"


def regrade_task_key(round_no: int) -> str:
    return f"regrade-round-{round_no:02d}"


def review_phase_dir(state_root: Path, round_no: int) -> Path:
    return round_dir(state_root, round_no) / "review"


def regrade_phase_dir(state_root: Path, round_no: int) -> Path:
    return round_dir(state_root, round_no) / "regrade"


def review_worker_score_path(state_root: Path, round_no: int) -> Path:
    """Where Ringer actually creates the review task's sandboxed cwd and where the worker's
    ``./score-worker.json`` therefore lands: ``<phase_dir>/<task-key>/score-worker.json`` --
    never ``<phase_dir>/score-worker.json`` (that is the phase dir itself, the task cwd's
    *parent*, which the worker has no authorized write access to)."""
    return review_phase_dir(state_root, round_no) / review_task_key(round_no) / "score-worker.json"


def regrade_worker_score_path(state_root: Path, round_no: int) -> Path:
    return regrade_phase_dir(state_root, round_no) / regrade_task_key(round_no) / "score-worker.json"


# --------------------------------------------------------------------------
# Plateau / stop conditions
# --------------------------------------------------------------------------


def is_plateaued(score_history: list[float], plateau_rounds: int = 2) -> bool:
    if len(score_history) < plateau_rounds + 1:
        return False
    recent = score_history[-(plateau_rounds + 1) :]
    non_improving = sum(1 for prev, curr in zip(recent, recent[1:]) if curr <= prev)
    return non_improving >= plateau_rounds


# --------------------------------------------------------------------------
# BLOCKED.md
# --------------------------------------------------------------------------


def write_blocked_md(
    state_root: Path,
    *,
    reason: str,
    score_history: list[float],
    last_confirmed_deductions: list[dict],
    commands_evidence: list[str],
    safe_next_action: str,
) -> Path:
    lines = [
        "---",
        "tags: [ringer-100, photo-curator, blocked]",
        "status: blocked",
        "---",
        "",
        "# Photo Curator Ringer-100 loop: BLOCKED",
        "",
        "## Reason",
        "",
        reason,
        "",
        "## Score history",
        "",
        (", ".join(str(s) for s in score_history) if score_history else "(no rounds completed)"),
        "",
        "## Last confirmed deductions",
        "",
    ]
    if last_confirmed_deductions:
        for deduction in last_confirmed_deductions:
            lines.append(f"- `{deduction.get('file')}:{deduction.get('line')}` "
                         f"[{deduction.get('severity')}] {deduction.get('summary')}")
    else:
        lines.append("- (none recorded)")
    lines += [
        "",
        "## Commands and evidence",
        "",
    ]
    for entry in commands_evidence:
        lines.append(f"- {entry}")
    if not commands_evidence:
        lines.append("- (none recorded)")
    lines += [
        "",
        "## Safe next action",
        "",
        safe_next_action,
        "",
    ]
    blocked_path = state_root / "BLOCKED.md"
    blocked_path.parent.mkdir(parents=True, exist_ok=True)
    blocked_path.write_text("\n".join(lines), encoding="utf-8")
    return blocked_path


# --------------------------------------------------------------------------
# Ringer manifest materialization
# --------------------------------------------------------------------------


def render_worker_prompt(rubric: dict, *, phase: str, round_no: int, extra_context: str = "") -> str:
    """Review and regrade MUST use the identical rubric/prompt -- built from one template."""
    header = (
        f"You are a read-only {phase} worker for round {round_no} of the bounded Ringer-100 loop. "
        "You are already running inside Ringer; do not invoke wsl, ringer.py, Ringside, skills, "
        "subagents, or another orchestration layer, and never modify any repository file -- read "
        "and score only.\n\n"
    )
    return header + rubric["review_protocol"] + ("\n\n" + extra_context if extra_context else "")


def build_review_manifest(
    config: dict,
    rubric: dict,
    round_no: int,
    state_root: Path,
    main_repo_root: Path,
    integration_worktree: Path,
) -> dict:
    phase_dir = review_phase_dir(state_root, round_no)
    task_key = review_task_key(round_no)
    add_dir = wsl_mnt_path_to_windows(integration_worktree)
    spec = render_worker_prompt(
        rubric,
        phase="review",
        round_no=round_no,
        extra_context=(
            f"The repository to review is the integration worktree at {integration_worktree.as_posix()} "
            "(granted to you via --add-dir); it is read-only for you -- never write inside it, and "
            "never run git commands against it. "
            "Write your findings as the score JSON schema documented in ringer-100/README.md to "
            "./score-worker.json in your current task working directory (create it if needed; do "
            "NOT write to the parent of your working directory or anywhere in the main repository) "
            "-- this is your own per-task artifact path, not the round's final score, and Ringer "
            "creates your task's working directory at <phase workdir>/<task key>, one level below "
            "the manifest's own workdir. The controller copies and normalizes your file into the "
            "authoritative score.json after this task completes. Also include an owned_files "
            "array: the exact list of repository file paths a later fix worker would need to edit "
            "to address your confirmed P0/P1/P2 findings, and (if any) a declared_fix_tests array "
            "naming test files the fix worker may add to or edit."
        ),
    )
    return {
        "run_name": config["run_name"],
        "workdir": str(phase_dir),
        "max_parallel": 1,
        "tasks": [
            {
                "key": task_key,
                "task_type": "code-review",
                "engine": config["worker"]["engine"],
                "model": config["worker"]["model"],
                "engine_args": add_dir_engine_args(add_dir),
                "spec": spec,
                "check": (
                    f"python3 {(RINGER_100_DIR / 'validate_score.py').as_posix()} "
                    f"--rubric {(main_repo_root / config['rubric_path']).as_posix()} "
                    "--score score-worker.json --no-current-counts"
                ),
                "expect_files": ["score-worker.json"],
                "timeout_s": config.get("worker_timeout_s", 1800),
                "verified": "review score JSON exists at ./score-worker.json in this task's own "
                "Ringer-created working directory and matches the pinned rubric hash/version/"
                "categories (a coarse, structural gate; the controller re-validates authoritatively "
                "after copying it into score.json)",
            }
        ],
    }


def build_fix_manifest(
    config: dict,
    round_no: int,
    state_root: Path,
    main_repo_root: Path,
    integration_worktree: Path,
    owned_files: list[str],
    accumulated_owned_files: list[str],
    declared_fix_tests: list[str],
    accumulated_declared_fix_tests: list[str],
    confirmed_deductions: list[dict],
) -> tuple[dict, Path]:
    phase_dir = round_dir(state_root, round_no) / "fix"
    allowlist_path = phase_dir / "allowlist.json"
    check_patch_path = phase_dir / "fix-check.patch"
    add_dir = wsl_mnt_path_to_windows(integration_worktree)
    deductions_text = "\n".join(
        f"- [{d.get('severity')}] {d.get('file')}:{d.get('line')} {d.get('summary')}" for d in confirmed_deductions
    )
    spec = (
        f"You are the fix worker for round {round_no} of the bounded Ringer-100 loop. The "
        f"repository to edit is the integration worktree at {integration_worktree.as_posix()} "
        "(granted to you via --add-dir) -- a real, isolated git checkout that accumulates fixes "
        "across rounds; edit files there directly. You MUST NEVER run any git command yourself "
        "(no add/commit/checkout/reset -- the controller owns all git operations on this "
        "worktree), and you must never touch ringer-100/rubric-v1.json, "
        "ringer-100/validate_score.py, ringer-100/validate_patch.py, ringer-100/verify_windows_undo.py, "
        "ringer-100/manifest.json, any .git path, or any test file not explicitly listed below.\n\n"
        "You own exactly these files and no others:\n"
        + "\n".join(f"- {f}" for f in owned_files)
        + "\n\nFix ONLY these confirmed review findings, minimally and without unrelated refactors:\n"
        + deductions_text
        + "\n\nWhen done, leave your changes uncommitted in the worktree; write nothing outside it."
    )
    # A coarse Ringer-level gate: the worker's edits must fall within every owned_files
    # allowlist declared so far this run (this round's plus every prior round's, since the
    # worktree accumulates). The controller separately isolates and validates only this
    # round's newly introduced delta against this round's own owned_files (see
    # compute_round_delta / validate_fix_patch in _run_round) before ever keeping it.
    check = (
        f"git -C {integration_worktree.as_posix()} diff --binary > {check_patch_path.as_posix()} && "
        f"python3 {(RINGER_100_DIR / 'validate_patch.py').as_posix()} "
        f"--patch {check_patch_path.as_posix()} --allowlist-file {allowlist_path.as_posix()}"
    )
    manifest = {
        "run_name": config["run_name"],
        "workdir": str(phase_dir),
        "max_parallel": 1,
        "tasks": [
            {
                "key": fix_task_key(round_no),
                "task_type": "code-fix",
                "engine": config["worker"]["engine"],
                "model": config["worker"]["model"],
                "engine_args": add_dir_engine_args(add_dir),
                "spec": spec,
                "check": check,
                "expect_files": [str(check_patch_path)],
                "timeout_s": config.get("worker_timeout_s", 1800),
                "verified": "the worker's accumulated worktree diff validates against the "
                "accumulated owned_files allowlist across all rounds so far (coarse Ringer-level "
                "gate); the controller separately isolates and validates only this round's newly "
                "introduced delta against this round's own owned_files before it is ever kept.",
            }
        ],
    }
    return manifest, allowlist_path


def build_regrade_manifest(
    config: dict,
    rubric: dict,
    round_no: int,
    state_root: Path,
    main_repo_root: Path,
    integration_worktree: Path,
    *,
    host_gates_hint: dict | None = None,
) -> dict:
    phase_dir = regrade_phase_dir(state_root, round_no)
    task_key = regrade_task_key(round_no)
    add_dir = wsl_mnt_path_to_windows(integration_worktree)
    gates_text = json.dumps(host_gates_hint, indent=2, sort_keys=True) if host_gates_hint else "(not yet available)"
    spec = render_worker_prompt(
        rubric,
        phase="regrade",
        round_no=round_no,
        extra_context=(
            f"The repository to regrade is the integration worktree at {integration_worktree.as_posix()} "
            "(granted to you via --add-dir); it is read-only for you -- never write inside it, and "
            "never run git commands against it. This is a regrade of the repository AFTER a fix "
            "was applied and host-verified. Score the current repository state fresh -- do not "
            "assume the prior review's findings still apply. For reference only, this round's "
            "actual host gate results (host-authoritative -- you cannot override them; the "
            "controller will forcibly replace your host_gates and host_evidence_paths fields "
            f"regardless of what you write) are:\n{gates_text}\n\n"
            "Write the score JSON to ./score-worker.json in your current task working directory "
            "(create it if needed; do NOT write to the parent of your working directory or "
            "anywhere in the main repository) -- your own per-task artifact path, not the round's "
            "final score. Ringer creates your task's working directory at <phase workdir>/<task "
            "key>, one level below the manifest's own workdir. Include owned_files (can be empty "
            "if you find no further confirmed P0/P1/P2 findings)."
        ),
    )
    return {
        "run_name": config["run_name"],
        "workdir": str(phase_dir),
        "max_parallel": 1,
        "tasks": [
            {
                "key": task_key,
                "task_type": "code-review",
                "engine": config["worker"]["engine"],
                "model": config["worker"]["model"],
                "engine_args": add_dir_engine_args(add_dir),
                "spec": spec,
                "check": (
                    f"python3 {(RINGER_100_DIR / 'validate_score.py').as_posix()} "
                    f"--rubric {(main_repo_root / config['rubric_path']).as_posix()} "
                    "--score score-worker.json --no-current-counts"
                ),
                "expect_files": ["score-worker.json"],
                "timeout_s": config.get("worker_timeout_s", 1800),
                "verified": "regrade score JSON exists at ./score-worker.json in this task's own "
                "Ringer-created working directory and is structurally validated against the "
                "identical pinned rubric (coarse gate); the controller forcibly overwrites "
                "host_gates/host_evidence_paths with host-authoritative values and re-validates "
                "authoritatively after copying it into score.json",
            }
        ],
    }


# --------------------------------------------------------------------------
# Manifest feasibility -- host-only structural lint (no models, no subprocess)
# --------------------------------------------------------------------------

_REQUIRED_TASK_FIELDS = ("key", "task_type", "engine", "model", "spec", "check", "expect_files", "timeout_s", "verified")
_UNVERIFIABLE_CHECKS = {"true", "exit 0", "echo done", ""}


def validate_manifest_shape(manifest: dict) -> list[str]:
    """Host-only structural lint for a materialized Ringer manifest -- no model call, no
    subprocess, no filesystem access, and no invocation of ``ringer.py lint`` itself (this loop
    never spawns another orchestration layer). Checks the fields a real Ringer manifest needs:
    run_name/workdir/tasks present, every task has the required fields, no unverifiable checks,
    and at least one expected deliverable per task."""
    problems: list[str] = []
    if not manifest.get("run_name"):
        problems.append("manifest is missing run_name")
    if not manifest.get("workdir"):
        problems.append("manifest is missing workdir")
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        problems.append("manifest has no tasks")
        return problems
    for task in tasks:
        key = task.get("key", "<no key>")
        for field_name in _REQUIRED_TASK_FIELDS:
            if not task.get(field_name):
                problems.append(f"task {key!r} is missing required field {field_name!r}")
        check = task.get("check", "")
        if isinstance(check, str) and check.strip() in _UNVERIFIABLE_CHECKS:
            problems.append(f"task {key!r} has an unverifiable check ({check!r})")
        expect_files = task.get("expect_files")
        if not isinstance(expect_files, list) or not expect_files:
            problems.append(f"task {key!r} declares no expect_files")
        spec = task.get("spec", "")
        if not isinstance(spec, str) or len(spec.strip()) < 20:
            problems.append(f"task {key!r} spec is missing or too short to be self-contained")
    return problems


def materialize_round_manifests_for_lint(
    config: dict,
    rubric: dict,
    round_no: int,
    state_root: Path,
    main_repo_root: Path,
    integration_worktree: Path,
) -> dict[str, list[str]]:
    """Host-only helper: materializes representative review/fix/regrade manifests for a round
    and structurally validates them -- no subprocess, no model call, no ringer.py invocation.
    Keeps the materialized manifests honest without spawning another orchestration layer."""
    placeholder_owned_files = ["app/example.py"]
    placeholder_deductions = [{"severity": "P1", "file": "app/example.py", "line": 1, "summary": "placeholder"}]
    review_manifest = build_review_manifest(config, rubric, round_no, state_root, main_repo_root, integration_worktree)
    fix_manifest, _allowlist_path = build_fix_manifest(
        config,
        round_no,
        state_root,
        main_repo_root,
        integration_worktree,
        placeholder_owned_files,
        placeholder_owned_files,
        [],
        [],
        placeholder_deductions,
    )
    regrade_manifest = build_regrade_manifest(
        config, rubric, round_no, state_root, main_repo_root, integration_worktree, host_gates_hint={}
    )
    return {
        "review": validate_manifest_shape(review_manifest),
        "fix": validate_manifest_shape(fix_manifest),
        "regrade": validate_manifest_shape(regrade_manifest),
    }


# --------------------------------------------------------------------------
# ringer.py lint/run wrappers
# --------------------------------------------------------------------------


def ringer_lint(ringer_root: Path, manifest_path: Path, *, run: RunFunc = run_subprocess) -> None:
    ringer_py = str(ringer_root / "ringer.py")
    proc = run([sys.executable, ringer_py, "lint", str(manifest_path)])
    if proc.returncode != 0:
        raise LoopError(f"ringer.py lint failed for {manifest_path}: {proc.stdout}\n{proc.stderr}")


def ringer_run(
    ringer_root: Path,
    manifest_path: Path,
    *,
    identity: str = "codex-orchestrator",
    run: RunFunc = run_subprocess,
) -> subprocess.CompletedProcess:
    ringer_py = str(ringer_root / "ringer.py")
    proc = run([sys.executable, ringer_py, "run", str(manifest_path), "--identity", identity])
    if proc.returncode != 0:
        raise LoopError(f"ringer.py run failed for {manifest_path}: {proc.stdout}\n{proc.stderr}")
    return proc


# --------------------------------------------------------------------------
# Host verification
# --------------------------------------------------------------------------

_WINDOWS_UNDO_SCREENSHOT_NAMES = ("delete-review.png", "after-delete.png", "after-undo.png")


def run_host_verification(
    config: dict,
    main_repo_root: Path,
    integration_worktree: Path,
    round_no: int,
    state_root: Path,
    *,
    review_score: dict | None = None,
    run: RunFunc = run_subprocess,
) -> tuple[dict, list[str], list[str]]:
    """Run every configured host gate command against the integration worktree.

    Uses the main checkout's resolved Windows venv interpreter (the worktree never has the
    gitignored .venv) but sets cwd to the integration worktree, so every gate exercises this
    round's actual accumulated fixes. Returns (gate_results, evidence_lines, host_evidence_paths)
    -- the host_evidence_paths are computed here, by the controller, never taken from a worker.

    Every command whose ``cmd[0]`` is the Windows venv Python is a Windows-native process: WSL
    interop resolves that executable's own path automatically, but never translates its
    arguments, so every absolute WSL filesystem argument bound for that process (the verifier
    script, ``--round-dir``, ``--repo-root``, ``--state-root``) is converted here via
    ``wsl_mnt_path_to_windows`` -- fail closed if one isn't representable. Relative pytest
    targets/flags are left untouched, and ``host_evidence_paths`` below is built from the
    original (unconverted) WSL ``state_root``/``windows_round_dir``, since it is read back by the
    WSL controller, never by the Windows process.
    """
    venv_python = resolve_windows_venv_python(main_repo_root)
    # ringer-100/*.py (this loop's own orchestration tooling) is never assumed to exist inside
    # the integration worktree -- it is only ever tracked/committed in the main checkout, so
    # every reference to it is built as an absolute path there, never a worktree-relative one.
    # main_repo_root is already absolute (main() resolves it under WSL before the loop starts),
    # so a plain join is enough -- an extra .resolve() here would re-run path normalization under
    # whichever Python happens to be calling this function, which is exactly the kind of
    # environment-dependent surprise this function must not depend on.
    substitutions = {
        "{VENV_PYTHON}": str(venv_python),
        "{VERIFY_WINDOWS_UNDO_SCRIPT}": str(main_repo_root / "ringer-100" / "verify_windows_undo.py"),
    }
    gate_results: dict[str, bool] = {}
    evidence: list[str] = []
    host_evidence_paths: list[str] = []
    windows_round_dir = round_dir(state_root, round_no) / "windows-verify"
    targeted_tests = (review_score or {}).get("targeted_tests", [])

    for entry in config["host_verification"]["commands"]:
        name = entry["name"]
        # Only commands invoking the Windows venv Python need their absolute WSL path arguments
        # translated -- other host gates (e.g. plain `git`) run under WSL and read /mnt/... fine.
        windows_native = entry["cmd"][:1] == ["{VENV_PYTHON}"]

        cmd = []
        for part in entry["cmd"]:
            value = substitutions.get(part, part)
            if windows_native and part == "{VERIFY_WINDOWS_UNDO_SCRIPT}":
                value = wsl_mnt_path_to_windows(value)
            cmd.append(value)

        if entry.get("round_dir_arg"):
            round_dir_value = str(windows_round_dir)
            if windows_native:
                round_dir_value = wsl_mnt_path_to_windows(round_dir_value)
            cmd = cmd + [round_dir_value]
        if entry.get("pass_repo_and_state_root"):
            repo_root_value = str(integration_worktree)
            state_root_value = str(state_root)
            if windows_native:
                repo_root_value = wsl_mnt_path_to_windows(repo_root_value)
                state_root_value = wsl_mnt_path_to_windows(state_root_value)
            cmd = cmd + ["--repo-root", repo_root_value, "--state-root", state_root_value]
        if entry.get("targets_from_review"):
            if not targeted_tests:
                gate_results[name] = True
                evidence.append(f"{name}: skipped (review named no targeted tests)")
                continue
            cmd = cmd + list(targeted_tests)
        proc = run(cmd, cwd=str(integration_worktree))
        passed = proc.returncode == 0
        gate_results[name] = passed
        evidence.append(f"{name}: `{' '.join(cmd)}` -> exit {proc.returncode}")
        if not passed:
            evidence.append(f"{name} stderr: {proc.stderr[-2000:] if proc.stderr else '(none)'}")
        if name == "windows_undo_verified" and passed:
            host_evidence_paths = [str(windows_round_dir / "evidence.json")] + [
                str(windows_round_dir / screenshot) for screenshot in _WINDOWS_UNDO_SCREENSHOT_NAMES
            ]

    return gate_results, evidence, host_evidence_paths


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------


@dataclass
class LoopOrchestrator:
    config: dict
    rubric: dict
    repo_root: Path  # the user's main checkout; touched only for the clean-gate and the single final apply
    ringer_root: Path
    state_root: Path
    run: RunFunc = field(default=run_subprocess)
    integration_worktree: Path | None = field(default=None)

    def dry_run(self) -> dict:
        """Print the plan for the next round without any mutation or model calls."""
        state = load_state(self.state_root)
        next_round = 1 if state is None else state.get("next_round", 1)
        preview_worktree = (
            Path(state["integration_worktree"]) if state and state.get("integration_worktree")
            else integration_worktree_dir(self.state_root)
        )
        review_manifest = build_review_manifest(
            self.config, self.rubric, next_round, self.state_root, self.repo_root, preview_worktree
        )
        plan = {
            "next_round": next_round,
            "run_name": self.config["run_name"],
            "max_rounds": self.config["max_rounds"],
            "plateau_rounds": self.config["plateau_rounds"],
            "score_history": state.get("score_history", []) if state else [],
            "integration_worktree_preview": str(preview_worktree),
            "review_manifest_preview": review_manifest,
        }
        print(json.dumps(plan, indent=2))
        return plan

    def assert_clean_start(self) -> None:
        assert_clean_repo(self.repo_root, self.state_root, run=self.run)

    def _ensure_integration_worktree(self, state: dict) -> None:
        existing = state.get("integration_worktree")
        if existing:
            self.integration_worktree = Path(existing)
            return
        worktree_dir, base_commit = create_integration_worktree(self.repo_root, self.state_root, run=self.run)
        self.integration_worktree = worktree_dir
        state["integration_worktree"] = str(worktree_dir)
        state["base_commit"] = base_commit
        state.setdefault("all_owned_files", [])
        state.setdefault("all_declared_fix_tests", [])
        save_state(self.state_root, state)

    def _run_round(self, round_no: int, state: dict) -> dict:
        integration_worktree = self.integration_worktree
        if integration_worktree is None:
            raise LoopError("no integration worktree is set; _ensure_integration_worktree must run first")
        rdir = round_dir(self.state_root, round_no)
        commands_evidence: list[str] = []
        rubric_path = self.repo_root / self.config["rubric_path"]

        # --- Review phase -------------------------------------------------------
        review_manifest = build_review_manifest(
            self.config, self.rubric, round_no, self.state_root, self.repo_root, integration_worktree
        )
        review_manifest_path = rdir / "review-manifest.json"
        atomic_write_json(review_manifest_path, review_manifest)
        ringer_lint(self.ringer_root, review_manifest_path, run=self.run)
        ringer_run(self.ringer_root, review_manifest_path, run=self.run)

        review_score_path = review_worker_score_path(self.state_root, round_no)
        if not review_score_path.is_file():
            raise LoopError(f"expected review worker score was not produced: {review_score_path}")
        score_path = rdir / "review" / "score.json"
        atomic_write_json(score_path, json.loads(review_score_path.read_text(encoding="utf-8")))

        review_score, review_result = validate_and_load_score(
            rubric_path, score_path, repo_root=integration_worktree, state_root=self.state_root,
            check_current_counts=False,
        )
        if not review_result.ok:
            raise LoopError("review score failed validation: " + "; ".join(review_result.failures))

        confirmed_deductions = [
            f for f in review_score.get("findings", []) if f.get("severity") in validate_score.BLOCKING_SEVERITIES
        ]

        if not confirmed_deductions:
            # Nothing left to fix; this round's total IS the regraded total.
            return {
                "round": round_no,
                "score_total": review_score.get("total"),
                "status": "no_confirmed_deductions",
                "commands_evidence": commands_evidence,
                "last_confirmed_deductions": [],
            }

        owned_files, declared_fix_tests = validate_and_load_allowlist(review_score)
        accumulated_owned_files = sorted(set(state.get("all_owned_files", [])) | set(owned_files))
        accumulated_declared_fix_tests = sorted(set(state.get("all_declared_fix_tests", [])) | set(declared_fix_tests))

        # --- Fix phase: direct edit of the persistent integration worktree ------
        fix_phase_dir = rdir / "fix"
        pre_tree = snapshot_worktree_tree(integration_worktree, fix_phase_dir, "pre", run=self.run)
        atomic_write_json(fix_phase_dir / "round-trees.json", {"pre_tree": pre_tree, "post_tree": None})

        fix_manifest, allowlist_path = build_fix_manifest(
            self.config,
            round_no,
            self.state_root,
            self.repo_root,
            integration_worktree,
            owned_files,
            accumulated_owned_files,
            declared_fix_tests,
            accumulated_declared_fix_tests,
            confirmed_deductions,
        )
        atomic_write_json(
            allowlist_path,
            {"owned_files": accumulated_owned_files, "declared_fix_tests": accumulated_declared_fix_tests},
        )
        fix_manifest_path = rdir / "fix-manifest.json"
        atomic_write_json(fix_manifest_path, fix_manifest)
        ringer_lint(self.ringer_root, fix_manifest_path, run=self.run)
        ringer_run(self.ringer_root, fix_manifest_path, run=self.run)

        round_delta, post_tree = compute_round_delta(integration_worktree, fix_phase_dir, pre_tree, run=self.run)
        round_delta_path = fix_phase_dir / "round-delta.patch"
        atomic_write_text(round_delta_path, round_delta)
        atomic_write_json(fix_phase_dir / "round-trees.json", {"pre_tree": pre_tree, "post_tree": post_tree})

        try:
            validate_fix_patch(round_delta_path, owned_files, declared_fix_tests)
        except LoopError:
            rollback_round_delta(integration_worktree, fix_phase_dir, round_delta_path, pre_tree, run=self.run)
            raise

        if not confirm_round_delta_reversible(integration_worktree, round_delta_path, run=self.run):
            rollback_round_delta(integration_worktree, fix_phase_dir, round_delta_path, pre_tree, run=self.run)
            raise LoopError(
                f"round {round_no}: this round's fix delta is not cleanly reversible; rolled back "
                "before host verification"
            )

        # --- Host verification (targets the integration worktree) ---------------
        gate_results, host_evidence, host_evidence_paths = run_host_verification(
            self.config, self.repo_root, integration_worktree, round_no, self.state_root,
            review_score=review_score, run=self.run,
        )
        commands_evidence.extend(host_evidence)
        atomic_write_json(rdir / "host-gates.json", gate_results)
        atomic_write_json(
            rdir / "host-evidence.json", {"host_evidence_paths": host_evidence_paths, "log": host_evidence}
        )

        if not all(gate_results.values()):
            rollback_round_delta(integration_worktree, fix_phase_dir, round_delta_path, pre_tree, run=self.run)
            failed_gates = [name for name, passed in gate_results.items() if not passed]
            return {
                "round": round_no,
                "score_total": review_score.get("total"),
                "status": "host_verification_failed",
                "failed_gates": failed_gates,
                "commands_evidence": commands_evidence,
                "last_confirmed_deductions": confirmed_deductions,
                "pre_tree": pre_tree,
                "post_tree": post_tree,
            }

        # --- Regrade (identical rubric/prompt to review; host authority forced) -
        regrade_manifest = build_regrade_manifest(
            self.config, self.rubric, round_no, self.state_root, self.repo_root, integration_worktree,
            host_gates_hint=gate_results,
        )
        regrade_manifest_path = rdir / "regrade-manifest.json"
        atomic_write_json(regrade_manifest_path, regrade_manifest)
        ringer_lint(self.ringer_root, regrade_manifest_path, run=self.run)
        ringer_run(self.ringer_root, regrade_manifest_path, run=self.run)

        regrade_score_path_worker = regrade_worker_score_path(self.state_root, round_no)
        if not regrade_score_path_worker.is_file():
            raise LoopError(f"expected regrade worker score was not produced: {regrade_score_path_worker}")
        worker_regrade_score = json.loads(regrade_score_path_worker.read_text(encoding="utf-8"))
        # Host authority: workers cannot manufacture or override host_gates/host_evidence_paths.
        final_regrade_score = dict(worker_regrade_score)
        final_regrade_score["host_gates"] = gate_results
        final_regrade_score["host_evidence_paths"] = host_evidence_paths
        regrade_score_path = rdir / "regrade" / "score.json"
        atomic_write_json(regrade_score_path, final_regrade_score)

        regrade_score, regrade_result = validate_and_load_score(
            rubric_path, regrade_score_path, repo_root=integration_worktree, state_root=self.state_root,
            check_current_counts=True,
        )
        commands_evidence.append(f"validated {regrade_score_path}")

        if not regrade_result.ok:
            rollback_round_delta(integration_worktree, fix_phase_dir, round_delta_path, pre_tree, run=self.run)
            return {
                "round": round_no,
                "score_total": regrade_score.get("total"),
                "status": "regrade_failed",
                "commands_evidence": commands_evidence,
                "last_confirmed_deductions": confirmed_deductions,
                "regrade_ok": False,
                "regrade_failures": regrade_result.failures,
                "pre_tree": pre_tree,
                "post_tree": post_tree,
            }

        # Regrade validated: this round's delta is kept. Accumulate ownership for the final export.
        state["all_owned_files"] = accumulated_owned_files
        state["all_declared_fix_tests"] = accumulated_declared_fix_tests

        return {
            "round": round_no,
            "score_total": regrade_score.get("total"),
            "status": "completed",
            "commands_evidence": commands_evidence,
            "last_confirmed_deductions": confirmed_deductions,
            "regrade_ok": regrade_result.ok,
            "regrade_failures": regrade_result.failures,
            "pre_tree": pre_tree,
            "post_tree": post_tree,
        }

    def _export_success(self, state: dict) -> None:
        """On a validated 100: export the full cumulative binary patch from the integration
        worktree, validate it against every owned_files allowlist declared this run, then apply
        it exactly once to the still-clean main checkout -- leaving it uncommitted for
        human/Codex review. Never touches the main checkout before this point."""
        integration_worktree = self.integration_worktree
        if integration_worktree is None:
            raise LoopError("no integration worktree is set; cannot export the final patch")

        final_diff = snapshot_worktree_diff(integration_worktree, run=self.run)
        final_patch_path = self.state_root / "final-cumulative.patch"
        atomic_write_text(final_patch_path, final_diff)

        if not final_diff.strip():
            # Nothing was ever changed (e.g. round 1 found no confirmed deductions at all).
            return

        owned_files = state.get("all_owned_files", [])
        declared_fix_tests = state.get("all_declared_fix_tests", [])
        validate_fix_patch(final_patch_path, owned_files, declared_fix_tests)

        assert_clean_repo(self.repo_root, self.state_root, run=self.run)
        apply_patch(self.repo_root, final_patch_path, run=self.run)

    def run_loop(self, *, resume: bool) -> int:
        existing_state = load_state(self.state_root)
        if not resume and existing_state is not None:
            raise LoopError(
                f"prior state already exists at {self.state_root}/state.json; pass --resume to continue it"
            )
        state = existing_state if resume else None
        if state is None:
            state = {
                "run_name": self.config["run_name"],
                "rounds": [],
                "score_history": [],
                "status": "in_progress",
                "next_round": 1,
                "all_owned_files": [],
                "all_declared_fix_tests": [],
            }

        if state.get("status") in ("success", "blocked"):
            print(f"loop already concluded with status={state['status']!r}; nothing to do")
            return 0 if state["status"] == "success" else 1

        self._ensure_integration_worktree(state)

        max_rounds = self.config["max_rounds"]
        plateau_rounds = self.config["plateau_rounds"]
        result: dict = {}

        while state["next_round"] <= max_rounds:
            round_no = state["next_round"]
            try:
                result = self._run_round(round_no, state)

                state["rounds"].append(result)
                total = result.get("score_total")
                if isinstance(total, (int, float)):
                    state["score_history"].append(total)
                state["next_round"] = round_no + 1
                save_state(self.state_root, state)

                if total == 100 and result.get("status") == "completed" and result.get("regrade_ok"):
                    self._export_success(state)
                    state["status"] = "success"
                    save_state(self.state_root, state)
                    print(f"SUCCESS: round {round_no} achieved a validated 100")
                    return 0
            except LoopError as exc:
                write_blocked_md(
                    self.state_root,
                    reason=f"round {round_no} raised a safety/integration error: {exc}",
                    score_history=state["score_history"],
                    last_confirmed_deductions=[],
                    commands_evidence=[str(exc)],
                    safe_next_action=(
                        "Inspect the round's state directory and the integration worktree, fix the "
                        "underlying cause, and re-run with --resume. The main checkout is untouched; "
                        "the integration worktree is preserved exactly as it was."
                    ),
                )
                state["status"] = "blocked"
                save_state(self.state_root, state)
                return 1

            if is_plateaued(state["score_history"], plateau_rounds=plateau_rounds):
                write_blocked_md(
                    self.state_root,
                    reason=(
                        f"plateaued: the last {plateau_rounds} rounds did not improve the total score "
                        f"(history={state['score_history']})"
                    ),
                    score_history=state["score_history"],
                    last_confirmed_deductions=result.get("last_confirmed_deductions", []),
                    commands_evidence=result.get("commands_evidence", []),
                    safe_next_action=(
                        "Review the last round's confirmed deductions manually; the automated loop "
                        "cannot make further progress without a human decision. The main checkout is "
                        "untouched; the integration worktree is preserved."
                    ),
                )
                state["status"] = "blocked"
                save_state(self.state_root, state)
                return 1

        write_blocked_md(
            self.state_root,
            reason=f"reached max_rounds={max_rounds} without a validated 100",
            score_history=state["score_history"],
            last_confirmed_deductions=state["rounds"][-1].get("last_confirmed_deductions", []) if state["rounds"] else [],
            commands_evidence=state["rounds"][-1].get("commands_evidence", []) if state["rounds"] else [],
            safe_next_action=(
                "Review score history and remaining findings; consider a fresh bounded run after "
                "manual fixes. The main checkout is untouched; the integration worktree is preserved."
            ),
        )
        state["status"] = "blocked"
        save_state(self.state_root, state)
        return 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the next round's plan; no mutation, no model calls")
    parser.add_argument("--resume", action="store_true", help="continue a previously started run from its persisted state")
    parser.add_argument("--repo", type=Path, default=None, help="repository root (default: resolved for this host)")
    parser.add_argument("--ringer-root", type=Path, default=None, help="Ringer install root (default: resolved for this host)")
    parser.add_argument("--state-root", type=Path, default=None, help="override the loop's state/artifact root")
    parser.add_argument(
        "--manifest", type=Path, default=None, help="loop manifest.json (default: ringer-100/manifest.json)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    try:
        require_wsl_host(raw_argv)

        args = build_arg_parser().parse_args(argv)
        repo_root = (args.repo or default_repo_root()).resolve()
        ringer_root = (args.ringer_root or default_ringer_root()).resolve()
        manifest_path = (args.manifest or (RINGER_100_DIR / "manifest.json")).resolve()

        config = load_loop_manifest(manifest_path)
        assert_rubric_immutable(config, repo_root)
        state_root = (args.state_root or default_state_root(repo_root, config)).resolve()
        rubric_path = repo_root / config["rubric_path"]
        rubric = json.loads(rubric_path.read_text(encoding="utf-8"))

        orchestrator = LoopOrchestrator(
            config=config, rubric=rubric, repo_root=repo_root, ringer_root=ringer_root, state_root=state_root
        )

        if args.dry_run:
            orchestrator.dry_run()
            return 0

        if not args.resume:
            orchestrator.assert_clean_start()

        return orchestrator.run_loop(resume=args.resume)
    except LoopError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
