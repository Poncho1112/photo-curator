"""Contract check for review items 1 (last live copy) and 2 (containment).

Refuses to let a duplicate group be deleted unless the KEEP copy is really on
disk, still hashes to the group SHA-256, and every path sits under a folder the
user actually added.

Run it from anywhere with the project's Windows interpreter:
    .venv\\Scripts\\python.exe scripts/check_last_copy_containment.py

Exit 0 = every scenario held. Non-zero prints exactly which scenario broke.

This check deliberately re-implements the contract independently of tests/, so a
change that quietly weakens a unit test still fails here. It was written to fail
against the pre-fix code and is kept as a regression guard. It is not part of the
pytest suite.

If you ever use it to gate an agent that owns repo files, run a COPY from outside
that agent's worktree -- an agent that can edit its own check has no check.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILURES: list[str] = []
PASSES: list[str] = []


def scenario(name):
    def wrap(fn):
        try:
            fn()
        except AssertionError as exc:
            FAILURES.append(f"{name}: {exc}")
        except Exception:
            FAILURES.append(f"{name}: raised unexpectedly\n{traceback.format_exc()}")
        else:
            PASSES.append(name)
        return fn

    return wrap


from app.controllers.library_controller import LibraryController  # noqa: E402
from app.paths import AppPaths  # noqa: E402
from engine.database.models import PhotoRecord  # noqa: E402
from engine.database.repository import PhotoRepository  # noqa: E402
from engine.delete.delete_service import DeleteService  # noqa: E402
from engine.duplicates.exact_duplicates import sha256_file  # noqa: E402


class TrashRecorder:
    """Stand-in trash backend: records what WOULD be trashed, removes nothing."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def __call__(self, path: Path):
        self.calls.append(Path(path))
        return None


def build(tmp: Path, *, content=b"identical-photo-bytes", extra_root=None):
    """Return (controller, recorder, service, survivor_path, target_path, root)."""
    root = tmp / "library"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir(parents=True)
    survivor = root / "a" / "keep.jpg"
    target_dir = extra_root if extra_root is not None else root / "b"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "copy.jpg"
    survivor.write_bytes(content)
    target.write_bytes(content)
    digest = sha256_file(survivor)

    repository = PhotoRepository(":memory:")
    for path in (survivor, target):
        repository.insert(
            PhotoRecord(
                path=str(path),
                sha256=digest,
                size=path.stat().st_size,
                status="indexed",
                duplicate_group=digest[:8],
            )
        )
    paths = AppPaths.from_root(tmp / "appdata")
    controller = LibraryController(repository, paths)
    controller.set_roots([str(root)])
    recorder = TrashRecorder()
    service = DeleteService(paths.undo_logs / "delete-verify.jsonl", trash_fn=recorder)
    return controller, recorder, service, survivor, target, root


@scenario("happy path still deletes (guards against refuse-everything)")
def _happy():
    with tempfile.TemporaryDirectory() as raw:
        controller, recorder, service, survivor, target, _ = build(Path(raw))
        review = controller.delete_review()
        assert review, "delete_review returned nothing for a valid in-root group"
        results = controller.delete_duplicates(review, service)
        assert any(r.trashed for r in results), (
            "no target was trashed on the happy path; "
            f"results={[(str(r.source), r.trashed, r.error) for r in results]}"
        )
        assert target in recorder.calls, f"target {target} was never handed to the trash backend"
        assert survivor not in recorder.calls, "SURVIVOR WAS TRASHED on the happy path"


@scenario("item 1: survivor missing from disk must refuse the group")
def _missing_survivor():
    with tempfile.TemporaryDirectory() as raw:
        controller, recorder, service, survivor, target, _ = build(Path(raw))
        survivor.unlink()  # deleted in Explorer / unplugged volume
        review = controller.delete_review()
        assert len(review) == 0, "delete_review offered a group whose KEEP file is gone from disk"
        skips = getattr(controller, "last_delete_review_skips", ())
        assert skips, "no skip reason recorded for the missing-survivor group"
        try:
            controller.delete_duplicates(review, service)
        except ValueError:
            pass
        assert recorder.calls == [], (
            f"LAST LIVE COPY WAS TRASHED with the survivor missing: {recorder.calls}"
        )


@scenario("item 1: stale catalog row (survivor bytes changed) must refuse the group")
def _survivor_hash_mismatch():
    with tempfile.TemporaryDirectory() as raw:
        controller, recorder, service, survivor, target, _ = build(Path(raw))
        review = controller.delete_review()
        survivor.write_bytes(b"different-bytes-entirely")  # catalog sha is now stale
        try:
            controller.delete_duplicates(review, service)
        except ValueError:
            pass
        assert recorder.calls == [], (
            "targets were trashed although the survivor no longer matches the "
            f"group SHA-256: {recorder.calls}"
        )


@scenario("item 1: DeleteService.delete_groups refuses a missing survivor directly")
def _service_level_guard():
    from engine.delete.delete_service import DeleteGroup

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        root = tmp / "library"
        (root / "b").mkdir(parents=True)
        survivor = root / "gone.jpg"
        target = root / "b" / "copy.jpg"
        target.write_bytes(b"bytes")
        digest = sha256_file(target)
        recorder = TrashRecorder()
        service = DeleteService(tmp / "log.jsonl", trash_fn=recorder)
        group = DeleteGroup(survivor=survivor, sha256=digest, targets=(target,))
        results = service.delete_groups([group], roots=[str(root)])
        assert recorder.calls == [], (
            f"delete_groups trashed a target while its survivor does not exist: {recorder.calls}"
        )
        assert results and all(not r.trashed for r in results), "expected all targets refused"
        assert any(r.error for r in results), "refusal reported no error text"


@scenario("item 2: target outside the added roots must not be trashed")
def _containment():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        outside = tmp / "outside-the-tree"
        controller, recorder, service, survivor, target, root = build(tmp, extra_root=outside)
        # target lives outside `root`, exactly as a junction-resolved path would
        try:
            controller.delete_duplicates(controller.delete_review(), service)
        except ValueError:
            pass
        assert recorder.calls == [], (
            f"a path outside every added folder was trashed: {recorder.calls}"
        )


@scenario("item 2: empty roots must refuse every deletion")
def _empty_roots():
    with tempfile.TemporaryDirectory() as raw:
        controller, recorder, service, survivor, target, _ = build(Path(raw))
        review = controller.delete_review()
        controller.set_roots([])
        try:
            controller.delete_duplicates(review, service)
        except ValueError:
            pass
        assert recorder.calls == [], (
            f"deletion proceeded with no added folders configured: {recorder.calls}"
        )


@scenario("item 2: is_contained honours as-selected roots and rejects escapes")
def _fsutil():
    from engine.fsutil import is_contained

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        root = tmp / "lib"
        (root / "sub").mkdir(parents=True)
        inside = root / "sub" / "x.jpg"
        inside.write_bytes(b"x")
        outside = tmp / "elsewhere" / "x.jpg"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(b"x")
        assert is_contained(inside, [root]), "a real in-tree path was rejected"
        assert not is_contained(outside, [root]), "an out-of-tree path was accepted"
        assert not is_contained(inside, []), "empty roots must contain nothing"


for name in PASSES:
    print(f"PASS  {name}")
for failure in FAILURES:
    print(f"FAIL  {failure}")

if FAILURES:
    print(f"\n{len(FAILURES)} of {len(PASSES) + len(FAILURES)} contract scenarios FAILED")
    raise SystemExit(1)
print(f"\nall {len(PASSES)} contract scenarios passed")
