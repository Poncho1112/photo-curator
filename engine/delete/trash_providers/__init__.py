"""Platform trash provider selection for :class:`DeleteService`'s default trash_fn.

macOS and Windows return a real, restorable destination path (native
Recycle Bin/Trash, or an app-managed quarantine folder as a Windows
fallback), so files trashed through the default path are reversible via
``UndoDeleteService``. Linux is not yet covered by a native provider (see
``docs/Milestone-4-Scope.md``) and still delegates to ``send2trash``, which
returns ``None`` - Linux deletions remain unrestorable until that
follow-up lands.
"""

from __future__ import annotations

import functools
import platform
from collections.abc import Callable
from pathlib import Path

from app.paths import AppPaths


def select_default_trash_fn() -> Callable[[Path], Path | None]:
    """Pick the recoverable trash function for the running platform."""
    system = platform.system()

    if system == "Darwin":
        from .macos import trash_to_macos_trash

        return trash_to_macos_trash

    if system == "Windows":
        from .windows import trash_to_windows_bin

        quarantine_dir = AppPaths.default().root / "trash"
        return functools.partial(trash_to_windows_bin, quarantine_dir=quarantine_dir)

    from send2trash import send2trash

    def _linux_fallback(source: Path) -> Path | None:
        send2trash(source)
        return None

    return _linux_fallback
