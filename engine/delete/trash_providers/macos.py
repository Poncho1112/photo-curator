"""macOS trash provider using NSFileManager's native trash call.

Uses the same underlying Cocoa API ``send2trash`` calls on macOS
(``NSFileManager.trashItemAtURL:resultingItemURL:error:``), but keeps the
resulting URL that a bare ``send2trash()`` call discards. That API renames
on name collision and reports the final destination by design, so once we
call it directly there is no path-capture problem to solve on this
platform - unlike Windows, no fallback path is needed here.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


class MacOSTrashError(OSError):
    """Raised when NSFileManager reports a file could not be trashed."""


def _call_ns_file_manager_trash(path: Path) -> tuple[bool, Path | None, str | None]:
    """Call NSFileManager's native trash API via pyobjc.

    Returns (success, resulting_path, error_detail). Kept separate from
    :func:`trash_to_macos_trash` so tests can inject a fake without pyobjc
    installed.
    """
    try:
        from Foundation import NSFileManager, NSURL
    except ImportError as exc:
        raise ImportError(
            "Trashing files on macOS requires pyobjc-framework-Cocoa; "
            "install it with 'pip install pyobjc-framework-Cocoa'."
        ) from exc

    file_manager = NSFileManager.defaultManager()
    source_url = NSURL.fileURLWithPath_(str(path))
    # PyObjC returns each Objective-C out-parameter (resultingItemURL,
    # error) as an extra value alongside the BOOL return when the
    # corresponding argument is passed as None.
    success, resulting_url, error = file_manager.trashItemAtURL_resultingItemURL_error_(
        source_url, None, None
    )
    resulting_path = Path(str(resulting_url.path())) if resulting_url is not None else None
    detail = error.localizedDescription() if error is not None else None
    return bool(success), resulting_path, detail


def trash_to_macos_trash(
    path: Path,
    call: Callable[[Path], tuple[bool, Path | None, str | None]] = _call_ns_file_manager_trash,
) -> Path:
    """Move ``path`` to the Trash via NSFileManager, returning its new location.

    Raises ImportError if pyobjc-framework-Cocoa is not installed, and
    MacOSTrashError if NSFileManager reports failure or omits a
    destination on success (defensive - not expected in practice).
    """
    success, resulting_path, detail = call(path)
    if not success or resulting_path is None:
        raise MacOSTrashError(f"failed to trash {path}: {detail or 'unknown error'}")
    return resulting_path
