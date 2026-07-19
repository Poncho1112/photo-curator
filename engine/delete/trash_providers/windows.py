"""Windows trash provider: IFileOperation with a progress sink that
captures the recycled destination, falling back to an app-managed
quarantine folder when pywin32/COM is unavailable, or when the operation
never touched the file.

Known verification gap (see docs/Milestone-4-Scope.md): this module has
not been exercised against a live IFileOperation call, only against
mocked ``call`` functions in tests/test_trash_providers_windows.py. The
COM progress-sink wiring (class shape, method list, ``PostDeleteItem``
signature) follows the documented Win32 interface, but pywin32's
IFileOperation sink support has been inconsistent across versions
historically - real-machine verification on Windows is required before
this is considered done, per the milestone's acceptance criteria.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable
from pathlib import Path


class WindowsTrashError(OSError):
    """Raised when IFileOperation reports a file could not be trashed."""


def _call_ifileoperation_trash(path: Path) -> Path | None:
    """Recycle ``path`` via IFileOperation, returning the recycled path.

    Returns None if the operation ran but the progress sink never
    reported a destination - the caller decides whether that means the
    file was untouched (safe to fall back) or something went wrong (see
    :func:`trash_to_windows_bin`). Raises ImportError if pywin32 is not
    installed.
    """
    try:
        import pythoncom
        import win32com.server.util
        from win32com.shell import shell, shellcon
    except ImportError as exc:
        raise ImportError(
            "Native Recycle Bin integration on Windows requires pywin32; "
            "install it with 'pip install pywin32'."
        ) from exc

    captured: dict[str, Path] = {}

    class _ProgressSink:
        _com_interfaces_ = [shell.IID_IFileOperationProgressSink]
        _public_methods_ = [
            "StartOperations",
            "FinishOperations",
            "PreRenameItem",
            "PostRenameItem",
            "PreMoveItem",
            "PostMoveItem",
            "PreCopyItem",
            "PostCopyItem",
            "PreDeleteItem",
            "PostDeleteItem",
            "PreNewItem",
            "PostNewItem",
            "UpdateProgress",
            "ResumeTimer",
            "PauseTimer",
            "ResetTimer",
        ]

        def StartOperations(self):
            pass

        def FinishOperations(self, hr_result):
            pass

        def PreRenameItem(self, flags, item, new_name):
            pass

        def PostRenameItem(self, flags, item, new_name, hr, new_item):
            pass

        def PreMoveItem(self, flags, item, dest_folder, new_name):
            pass

        def PostMoveItem(self, flags, item, dest_folder, new_name, hr, new_item):
            pass

        def PreCopyItem(self, flags, item, dest_folder, new_name):
            pass

        def PostCopyItem(self, flags, item, dest_folder, new_name, hr, new_item):
            pass

        def PreDeleteItem(self, flags, item):
            pass

        def PostDeleteItem(self, flags, item, hr_delete, new_item):
            # Windows calls this with the newly created (recycled) item.
            if new_item is not None:
                try:
                    captured["path"] = Path(
                        new_item.GetDisplayName(shellcon.SIGDN_FILESYSPATH)
                    )
                except Exception:  # pragma: no cover - defensive against COM edge cases
                    pass

        def PreNewItem(self, flags, dest_folder, new_name):
            pass

        def PostNewItem(self, flags, dest_folder, new_name, template, attrs, hr, new_item):
            pass

        def UpdateProgress(self, work_total, work_so_far):
            pass

        def ResumeTimer(self):
            pass

        def PauseTimer(self):
            pass

        def ResetTimer(self):
            pass

    pythoncom.CoInitialize()
    try:
        file_op = pythoncom.CoCreateInstance(
            shell.CLSID_FileOperation,
            None,
            pythoncom.CLSCTX_INPROC_SERVER,
            shell.IID_IFileOperation,
        )
        file_op.SetOperationFlags(
            shellcon.FOF_ALLOWUNDO | shellcon.FOFX_RECYCLEONDELETE | shellcon.FOF_NO_UI
        )
        sink = win32com.server.util.wrap(_ProgressSink())
        file_op.Advise(sink)
        item = shell.SHCreateItemFromParsingName(str(path), None, shell.IID_IShellItem)
        file_op.DeleteItem(item, None)
        file_op.PerformOperations()
    finally:
        pythoncom.CoUninitialize()

    return captured.get("path")


def quarantine_fallback(path: Path, quarantine_dir: Path) -> Path:
    """Move ``path`` into an app-managed quarantine folder.

    Used when native Recycle Bin integration is unavailable, or ran
    without touching the file. Preserves the "always undoable" guarantee;
    the file will not appear in Explorer's Recycle Bin, which is a known,
    documented trade-off (see docs/Milestone-4-Scope.md).
    """
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    destination = quarantine_dir / f"{uuid.uuid4().hex}-{path.name}"
    shutil.move(str(path), str(destination))
    return destination


def trash_to_windows_bin(
    path: Path,
    quarantine_dir: Path,
    call: Callable[[Path], Path | None] = _call_ifileoperation_trash,
) -> Path:
    """Best-effort Windows trash.

    Tries the native Recycle Bin via IFileOperation first. Falls back to
    an app-managed quarantine folder only when it is safe to do so - either
    pywin32/COM is unavailable, or the operation ran but left the source
    file untouched. If the source file is gone but no destination was
    reported, this refuses to guess and raises instead, since the file may
    already be in the Recycle Bin untracked by our deletion log.
    """
    try:
        recycled = call(path)
    except ImportError:
        return quarantine_fallback(path, quarantine_dir)

    if recycled is not None:
        return recycled

    if path.exists():
        # IFileOperation ran but never removed the file (e.g. a UAC
        # prompt was cancelled, or the sink didn't fire) - the source is
        # untouched, so it is safe to fall back to the quarantine folder.
        return quarantine_fallback(path, quarantine_dir)

    raise WindowsTrashError(
        f"{path} was removed but IFileOperation's progress sink never "
        "reported a recycled destination; refusing to guess its location"
    )
