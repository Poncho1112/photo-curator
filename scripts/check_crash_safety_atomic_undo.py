"""Contract check for review items 3 (crash/exception safety) and 4 (atomic
undo-log rewrite, and no CoUninitialize on the Qt GUI thread).

A recycled file is never reported as skipped just because its log write failed;
the catalog and last_delete_log advance per file so a crash mid-batch cannot aim
Undo at the previous batch; no undo log is ever truncated in place; and COM is
never torn down on the calling thread.

It defines its own COM and trash fakes rather than importing from tests/, so a
weakened test fixture cannot weaken this check. The crash scenario uses a real
KeyboardInterrupt, which no `except Exception` can absorb.

Run it from anywhere with the project's Windows interpreter:
    .venv\\Scripts\\python.exe scripts/check_crash_safety_atomic_undo.py

Exit 0 = every scenario held. Non-zero prints exactly which scenario broke.

This check deliberately re-implements the contract independently of tests/, so a
change that quietly weakens a unit test still fails here. It was written to fail
against the pre-fix code and is kept as a regression guard. It is not part of the
pytest suite.

If you ever use it to gate an agent that owns repo files, run a COPY from outside
that agent's worktree -- an agent that can edit its own check has no check.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

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
from engine.delete.delete_service import DeleteGroup, DeleteService  # noqa: E402
from engine.duplicates.exact_duplicates import sha256_file  # noqa: E402


class TrashRecorder:
    """Records what WOULD be trashed; removes nothing. Optionally detonates."""

    def __init__(self, *, explode_on=None, exception=None) -> None:
        self.calls: list[Path] = []
        self.explode_on = explode_on
        self.exception = exception

    def __call__(self, path: Path):
        path = Path(path)
        if self.explode_on is not None and path == self.explode_on:
            raise self.exception
        self.calls.append(path)
        return None


def make_library(tmp: Path, *, target_count=1):
    """Build a catalog with one survivor and N in-root duplicate targets."""
    root = tmp / "library"
    root.mkdir(parents=True, exist_ok=True)
    content = b"identical-photo-bytes"
    survivor = root / "keep.jpg"
    survivor.write_bytes(content)
    targets = []
    for index in range(target_count):
        target = root / f"copy{index}.jpg"
        target.write_bytes(content)
        targets.append(target)
    digest = sha256_file(survivor)

    repository = PhotoRepository(":memory:")
    for path in (survivor, *targets):
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
    return controller, repository, paths, survivor, targets, digest


class LogFailingDeleteService(DeleteService):
    """Trashes normally, but the deletion log write fails for chosen sources."""

    fail_for: set = set()

    def _write_log(self, source, sha256, trashed_to, *, source_size):  # type: ignore[override]
        if not self.fail_for or Path(source) in self.fail_for:
            raise OSError("simulated deletion-log write failure")
        return super()._write_log(source, sha256, trashed_to, source_size=source_size)


@scenario("item 3: recycle succeeded but log failed = trashed-with-unknown-undo, not skipped")
def _log_failure_is_trashed():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        _, _, paths, survivor, targets, digest = make_library(tmp)
        recorder = TrashRecorder()
        service = LogFailingDeleteService(paths.undo_logs / "d.jsonl", trash_fn=recorder)
        group = DeleteGroup(survivor=survivor, sha256=digest, targets=tuple(targets))

        results = service.delete_groups([group], roots=[str(tmp / "library")])

        assert recorder.calls == targets, "the target was never actually handed to the trash backend"
        assert len(results) == 1, f"expected one result per target, got {results}"
        result = results[0]
        assert result.trashed is True, (
            "a file that really was recycled is being reported as skipped/failed just because "
            f"the log write failed; error={result.error!r}"
        )
        assert getattr(result, "undo_logged", None) is False, (
            "TrashResult must expose undo_logged=False so the caller can tell that this file "
            "is in the Recycle Bin with no undo entry"
        )
        assert result.error, "trashed-with-unknown-undo must still carry an explanatory error"


@scenario("item 3: a per-file log failure does not abort the rest of the batch")
def _batch_continues():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        _, _, paths, survivor, targets, digest = make_library(tmp, target_count=2)
        recorder = TrashRecorder()
        service = LogFailingDeleteService(paths.undo_logs / "d.jsonl", trash_fn=recorder)
        service.fail_for = {targets[0].resolve()}
        group = DeleteGroup(survivor=survivor, sha256=digest, targets=tuple(targets))

        results = service.delete_groups([group], roots=[str(tmp / "library")])

        assert len(results) == 2, f"batch stopped early: {results}"
        assert all(r.trashed for r in results), (
            f"a later file was skipped after an earlier file's log failure: "
            f"{[(str(r.source), r.trashed, r.error) for r in results]}"
        )
        assert getattr(results[1], "undo_logged", None) is True, (
            "the healthy file in the same batch must still be recorded as undoable"
        )


@scenario("item 3: a crash mid-batch still leaves the catalog and undo log correct per file")
def _crash_mid_batch():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        controller, repository, paths, survivor, targets, digest = make_library(tmp, target_count=2)
        first, second = targets
        # KeyboardInterrupt is a BaseException: no `except Exception` can hide it,
        # so this genuinely simulates the process dying mid-batch.
        recorder = TrashRecorder(explode_on=second, exception=KeyboardInterrupt())
        service = DeleteService(paths.undo_logs / "d.jsonl", trash_fn=recorder)

        review = controller.delete_review()
        assert review, "setup failure: no reviewable group"

        crashed = False
        try:
            controller.delete_duplicates(review, service)
        except KeyboardInterrupt:
            crashed = True
        assert crashed, "the simulated crash was swallowed; it must propagate"

        assert recorder.calls == [first], f"unexpected trash calls: {recorder.calls}"

        record = repository.get_by_path(str(first))
        assert record is not None and record.status == "deleted", (
            "a file already in the Recycle Bin is still catalogued as "
            f"{record.status if record else 'missing'!r}; the catalog must be updated per file, "
            "not after the whole batch"
        )
        assert controller.last_delete_log == service.deletion_log, (
            "last_delete_log was not advanced to this batch, so Undo Delete would aim at the "
            "PREVIOUS batch while this batch's files sit in the Recycle Bin"
        )
        # Parse the log as JSON: paths are backslash-escaped inside the JSON text,
        # so a raw substring match on str(path) can never succeed on Windows.
        logged_paths = {
            json.loads(line)["original_path"]
            for line in service.deletion_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        assert str(first.resolve()) in logged_paths, (
            f"the trashed file was never written to the deletion log; logged={logged_paths}"
        )


def _break_replace():
    """Make every atomic-rename primitive fail, whichever one the impl uses."""
    original_os, original_path = os.replace, pathlib.Path.replace

    def boom(*_args, **_kwargs):
        raise OSError("simulated replace failure")

    os.replace = boom
    pathlib.Path.replace = boom
    return original_os, original_path


def _restore_replace(saved):
    os.replace, pathlib.Path.replace = saved


@scenario("item 4: UndoDeleteService log rewrite is atomic (a failed replace preserves the log)")
def _atomic_delete_log():
    from engine.delete.undo_delete_service import UndoDeleteService

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        trashed = tmp / "recycled.jpg"
        trashed.write_bytes(b"payload")
        entry = {
            "original_path": str(tmp / "restored.jpg"),
            "sha256": sha256_file(trashed),
            "trashed_to": str(trashed),
            "trashed": True,
        }
        log = tmp / "delete.jsonl"
        before = json.dumps(entry) + "\n"
        log.write_text(before, encoding="utf-8")

        saved = _break_replace()
        try:
            try:
                UndoDeleteService(log, enable_default_legacy_locator=False).restore_all()
            except OSError:
                pass
        finally:
            _restore_replace(saved)

        after = log.read_text(encoding="utf-8")
        assert after == before, (
            "the deletion log was rewritten in place: a failure mid-rewrite destroyed it "
            f"(before={before!r} after={after!r}). Use tempfile + os.replace."
        )


@scenario("item 4: rename UndoService log rewrite is atomic too")
def _atomic_rename_log():
    from engine.rename.undo_service import UndoService

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        current = tmp / "renamed.jpg"
        current.write_bytes(b"payload")
        entry = {"source": str(tmp / "original.jpg"), "target": str(current)}
        log = tmp / "rename.jsonl"
        before = json.dumps(entry) + "\n"
        log.write_text(before, encoding="utf-8")

        saved = _break_replace()
        try:
            try:
                UndoService(log).restore_all()
            except OSError:
                pass
        finally:
            _restore_replace(saved)

        after = log.read_text(encoding="utf-8")
        assert after == before, (
            "the rename undo log was rewritten in place and lost on failure "
            f"(before={before!r} after={after!r})"
        )


@scenario("item 4: atomic_write_text preserves the original on failure and leaves no temp files")
def _atomic_helper():
    from engine.fsutil import atomic_write_text

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        target = tmp / "log.jsonl"
        target.write_text("original\n", encoding="utf-8")

        atomic_write_text(target, "replacement\n")
        assert target.read_text(encoding="utf-8") == "replacement\n", "successful write did not land"
        leftovers = [p.name for p in tmp.iterdir() if p.name != "log.jsonl"]
        assert not leftovers, f"temp files left behind after a successful write: {leftovers}"

        saved = _break_replace()
        try:
            try:
                atomic_write_text(target, "should-not-land\n")
            except OSError:
                pass
        finally:
            _restore_replace(saved)

        assert target.read_text(encoding="utf-8") == "replacement\n", (
            "a failed atomic write clobbered the existing file"
        )
        leftovers = [p.name for p in tmp.iterdir() if p.name != "log.jsonl"]
        assert not leftovers, f"temp files left behind after a failed write: {leftovers}"


def _counting_com_loader(counter, *, recycled_path=r"C:\$Recycle.Bin\S-1\$RFAKE.jpg"):
    """Minimal pywin32 stand-in that counts COM apartment calls."""

    class FakeFileOperation:
        def SetOperationFlags(self, flags):
            return None

        def DeleteItem(self, item, sink):
            return None

        def PerformOperations(self):
            return 0

        def GetAnyOperationsAborted(self):
            return False

    fileop = FakeFileOperation()

    class FakeShellcon:
        TSF_DELETE_RECYCLE_IF_POSSIBLE = 0x80
        SHGDN_FORPARSING = 0x8000
        FOF_NOCONFIRMATION = 16
        FOF_SILENT = 4
        FOF_NOERRORUI = 1024
        FOF_ALLOWUNDO = 64
        FOFX_EARLYFAILURE = 0x00100000
        FOFX_ADDUNDORECORD = 0x20000000
        FOFX_RECYCLEONDELETE = 0x00080000

    class FakeShell:
        CLSID_FileOperation = "CLSID_FileOperation"
        IID_IFileOperation = "IID_IFileOperation"
        IID_IFileOperationProgressSink = "IID_IFileOperationProgressSink"
        IID_IShellItem = "IID_IShellItem"

        @staticmethod
        def SHCreateItemFromParsingName(path, _bind, _iid):
            return SimpleNamespace(path=path)

    class FakePythoncom:
        CLSCTX_ALL = 1

        @staticmethod
        def CoInitialize():
            counter["init"] += 1
            return None

        @staticmethod
        def CoInitializeEx(flags=0):
            counter["init"] += 1
            return None

        @staticmethod
        def CoUninitialize():
            counter["uninit"] += 1
            return None

        @staticmethod
        def CoCreateInstance(clsid, unk, ctx, iid):
            return fileop

        @staticmethod
        def WrapObject(obj, iid):
            obj.new_item_path = recycled_path
            return obj

    class FakeDesignatedWrapPolicy:
        def _wrap_(self, obj):
            return None

    class FakePywintypes:
        com_error = type("FakeComError", (Exception,), {"strerror": "x", "hresult": -1})

    def loader():
        return {
            "pythoncom": FakePythoncom,
            "pywintypes": FakePywintypes,
            "DesignatedWrapPolicy": FakeDesignatedWrapPolicy,
            "shell": FakeShell,
            "shellcon": FakeShellcon,
        }

    return loader


@scenario("item 4: COM is never uninitialized on the calling (Qt GUI) thread")
def _no_couninitialize():
    from engine.delete.windows_recycle_bin import send_to_recycle_bin

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        counter = {"init": 0, "uninit": 0}
        loader = _counting_com_loader(counter)
        for index in range(3):
            source = tmp / f"photo{index}.jpg"
            source.write_bytes(b"data")
            send_to_recycle_bin(source, com_loader=loader)

        assert counter["uninit"] == 0, (
            f"CoUninitialize was called {counter['uninit']} time(s) on the calling thread; "
            "on the Qt GUI thread that can drop Qt's STA mid-batch"
        )
        assert counter["init"] <= 1, (
            f"COM was initialized {counter['init']} times for 3 files; initialize once per "
            "thread, not per file"
        )


for name in PASSES:
    print(f"PASS  {name}")
for failure in FAILURES:
    print(f"FAIL  {failure}")

if FAILURES:
    print(f"\n{len(FAILURES)} of {len(PASSES) + len(FAILURES)} contract scenarios FAILED")
    raise SystemExit(1)
print(f"\nall {len(PASSES)} contract scenarios passed")
