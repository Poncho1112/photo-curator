"""Windows Recycle Bin helpers: exact-path IFileOperation trash and legacy $I/$R lookup.

Supported $I metadata versions (public Recycle Bin format):

* **Version 1** (Vista / 7 / 8): little-endian ``uint64`` header ``1``, then
  file size (``uint64``), deletion FILETIME (``uint64``), and a fixed 520-byte
  UTF-16LE original path field (null-terminated, padded).
* **Version 2** (Windows 10+): little-endian ``uint64`` header ``2``, then
  file size (``uint64``), deletion FILETIME (``uint64``), path character count
  including the terminating NUL (``uint32``), then that many UTF-16LE code units.

Unknown header versions and truncated buffers are rejected. Paths are handled as
Unicode (``str`` / UTF-16LE). Production deletes use Shell ``IFileOperation`` with
recycle/undo flags and a progress sink that returns the recycled ``$R`` path;
legacy discovery is only for historical logs that recorded ``trashed_to=null``.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

# Supported $I header version integers (little-endian uint64 at offset 0).
SUPPORTED_I_HEADER_VERSIONS: frozenset[int] = frozenset({1, 2})

_I_V1_MIN_SIZE = 8 + 8 + 8 + 520  # header, size, filetime, fixed path field
_I_V2_MIN_SIZE = 8 + 8 + 8 + 4  # header, size, filetime, path length dword

# Shell operation flags (mirrored from shellcon / send2trash modern backend).
_FOF_NOCONFIRMATION = 16
_FOF_SILENT = 4
_FOF_NOERRORUI = 1024
_FOF_ALLOWUNDO = 64
_FOFX_EARLYFAILURE = 0x00100000
_FOFX_ADDUNDORECORD = 0x20000000
_FOFX_RECYCLEONDELETE = 0x00080000

# TSF_DELETE_RECYCLE_IF_POSSIBLE — refuse permanent delete in PreDeleteItem.
_TSF_DELETE_RECYCLE_IF_POSSIBLE = 0x00000080
_S_OK = 0
_E_FAIL = 0x80004005

WINDOWS_TRASH_BACKEND = "windows_ifileoperation"


@dataclass(frozen=True, slots=True)
class RecycleInfoMetadata:
    """Parsed fields from a Recycle Bin $I metadata file."""

    version: int
    original_path: str
    file_size: int
    deletion_filetime: int


def normalize_windows_path(path: str | Path) -> str:
    """Normalize a Windows filesystem path for equality comparison."""
    text = os.fspath(path)
    if text.startswith("\\\\?\\"):
        text = text[4:]
    text = text.replace("/", "\\")
    # Collapse duplicate separators except leading UNC.
    while "\\\\" in text and not text.startswith("\\\\"):
        text = text.replace("\\\\", "\\")
    return os.path.normcase(os.path.normpath(text))


def parse_i_file(data: bytes) -> RecycleInfoMetadata:
    """Parse a Recycle Bin ``$I`` metadata blob.

    Raises:
        ValueError: Unknown header version, truncated buffer, or invalid path.
    """
    if len(data) < 8:
        raise ValueError("$I metadata truncated: missing header")
    (version,) = struct.unpack_from("<Q", data, 0)
    if version not in SUPPORTED_I_HEADER_VERSIONS:
        raise ValueError(f"unsupported $I header version: {version}")

    if version == 1:
        if len(data) < _I_V1_MIN_SIZE:
            raise ValueError("$I v1 metadata truncated")
        file_size, deletion_filetime = struct.unpack_from("<QQ", data, 8)
        path_bytes = data[24 : 24 + 520]
        # Path is UTF-16LE, null-terminated within the fixed field.
        if len(path_bytes) % 2:
            path_bytes = path_bytes[:-1]
        original_path = path_bytes.decode("utf-16-le", errors="strict").split("\0", 1)[0]
    else:  # version == 2
        if len(data) < _I_V2_MIN_SIZE:
            raise ValueError("$I v2 metadata truncated: missing path length")
        file_size, deletion_filetime, path_chars = struct.unpack_from("<QQI", data, 8)
        if path_chars < 1:
            raise ValueError("$I v2 path length must be at least 1")
        path_byte_len = path_chars * 2
        path_start = 28
        if len(data) < path_start + path_byte_len:
            raise ValueError("$I v2 metadata truncated: path field shorter than declared length")
        path_bytes = data[path_start : path_start + path_byte_len]
        original_path = path_bytes.decode("utf-16-le", errors="strict").split("\0", 1)[0]

    if not original_path:
        raise ValueError("$I metadata has empty original path")
    return RecycleInfoMetadata(
        version=version,
        original_path=original_path,
        file_size=file_size,
        deletion_filetime=deletion_filetime,
    )


def build_i_file_bytes(
    original_path: str,
    file_size: int,
    *,
    version: int = 2,
    deletion_filetime: int = 0,
) -> bytes:
    """Build a synthetic ``$I`` blob for unit tests (not used in production)."""
    if version == 1:
        path_encoded = original_path.encode("utf-16-le") + b"\x00\x00"
        if len(path_encoded) > 520:
            raise ValueError("path too long for $I v1 fixed field")
        path_field = path_encoded.ljust(520, b"\x00")
        return struct.pack("<QQQ", 1, file_size, deletion_filetime) + path_field
    if version == 2:
        # Character count includes the terminating NUL.
        path_units = original_path + "\0"
        path_encoded = path_units.encode("utf-16-le")
        return (
            struct.pack("<QQQI", 2, file_size, deletion_filetime, len(path_units))
            + path_encoded
        )
    raise ValueError(f"unsupported $I header version: {version}")


def r_path_for_i_path(i_path: Path) -> Path:
    """Map a ``$I…`` metadata path to its sibling ``$R…`` content path."""
    name = i_path.name
    if not name.startswith("$I"):
        raise ValueError(f"not an $I metadata filename: {name}")
    return i_path.with_name("$R" + name[2:])


def iter_i_files(recycle_bin_roots: Iterable[Path]) -> list[Path]:
    """List ``$I*`` metadata files under one or more Recycle Bin SID directories."""
    found: list[Path] = []
    for root in recycle_bin_roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_file() and child.name.startswith("$I"):
                found.append(child)
    return found


def default_recycle_bin_roots(original_path: str | Path) -> list[Path]:
    """Return SID directories under ``{drive}\\$Recycle.Bin`` for *original_path*'s volume."""
    resolved = Path(original_path)
    # On Windows, drive is like "C:"; under tests we may get a POSIX path with no drive.
    drive = getattr(resolved, "drive", "") or ""
    if not drive:
        # Best-effort: treat anchor/root as volume (allows injected test layouts).
        anchor = resolved.anchor
        if not anchor:
            return []
        bin_root = Path(anchor) / "$Recycle.Bin"
    else:
        bin_root = Path(drive + os.sep) / "$Recycle.Bin"
    if not bin_root.is_dir():
        return []
    try:
        return [entry for entry in bin_root.iterdir() if entry.is_dir()]
    except OSError:
        return []


def locate_trashed_file(
    original_path: str | Path,
    *,
    expected_sha256: str | None = None,
    source_size: int | None = None,
    recycle_bin_roots: Sequence[Path] | None = None,
    hasher: Callable[[Path], str] | None = None,
) -> Path:
    """Locate a recycled ``$R`` file via conservative ``$I`` metadata matching.

    Matches normalized original path and, when provided, original file size.
    When *expected_sha256* is set, validates each ``$R`` candidate and keeps only
    hash matches. Refuses zero or ambiguous results (never picks "newest").

    Raises:
        OSError: No unique validated candidate.
    """
    from engine.duplicates.exact_duplicates import sha256_file as default_hasher

    hash_fn = hasher or default_hasher
    target_norm = normalize_windows_path(original_path)
    roots = (
        list(recycle_bin_roots)
        if recycle_bin_roots is not None
        else default_recycle_bin_roots(original_path)
    )
    if not roots:
        raise OSError(
            "no Recycle Bin SID directories found on the source volume; "
            "cannot locate recycled file for undo"
        )

    path_matches: list[tuple[Path, RecycleInfoMetadata]] = []
    for i_path in iter_i_files(roots):
        try:
            raw = i_path.read_bytes()
            meta = parse_i_file(raw)
        except (OSError, ValueError, UnicodeError):
            continue
        if normalize_windows_path(meta.original_path) != target_norm:
            continue
        if source_size is not None and meta.file_size != source_size:
            continue
        r_path = r_path_for_i_path(i_path)
        if not r_path.is_file():
            continue
        path_matches.append((r_path, meta))

    if not path_matches:
        raise OSError(
            "no recycle-bin candidate matches original path and size; cannot restore"
        )

    if expected_sha256:
        validated: list[Path] = []
        for r_path, _meta in path_matches:
            try:
                digest = hash_fn(r_path)
            except OSError:
                continue
            if digest == expected_sha256:
                validated.append(r_path)
        if not validated:
            raise OSError(
                "recycle-bin candidate(s) failed SHA-256 verification; cannot restore"
            )
        if len(validated) > 1:
            raise OSError(
                "multiple recycle-bin candidates match path, size, and hash; "
                "refusing ambiguous restore"
            )
        return validated[0]

    if len(path_matches) > 1:
        raise OSError(
            "multiple recycle-bin candidates match original path and size; "
            "refusing ambiguous restore"
        )
    return path_matches[0][0]


def make_legacy_locator(
    *,
    recycle_bin_roots: Sequence[Path] | None = None,
    hasher: Callable[[Path], str] | None = None,
) -> Callable[[dict[str, object]], Path]:
    """Build a log-entry → ``$R`` path locator for :class:`UndoDeleteService`."""

    def locator(entry: dict[str, object]) -> Path:
        original = entry.get("original_path")
        if original is None:
            raise OSError("deletion log entry missing original_path")
        size_value = entry.get("source_size")
        source_size: int | None
        if size_value is None:
            source_size = None
        else:
            source_size = int(size_value)  # type: ignore[arg-type]
        sha_value = entry.get("sha256")
        expected_sha256 = str(sha_value) if sha_value is not None else None
        roots = recycle_bin_roots
        if roots is None:
            # Prefer roots from the original path's volume when not injected.
            roots = None
        return locate_trashed_file(
            str(original),
            expected_sha256=expected_sha256,
            source_size=source_size,
            recycle_bin_roots=roots,
            hasher=hasher,
        )

    return locator


def _strip_extended_path_prefix(path: str) -> str:
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _load_com_modules() -> dict[str, object]:
    """Lazy-import pywin32 COM modules. Raises OSError if unavailable."""
    try:
        import pythoncom
        import pywintypes
        from win32com.server.policy import DesignatedWrapPolicy
        from win32com.shell import shell, shellcon
    except ImportError as exc:
        raise OSError(
            "Windows Recycle Bin delete requires pywin32 (install "
            "'send2trash[nativeLib]' or 'pywin32>=305' on Windows)"
        ) from exc
    return {
        "pythoncom": pythoncom,
        "pywintypes": pywintypes,
        "DesignatedWrapPolicy": DesignatedWrapPolicy,
        "shell": shell,
        "shellcon": shellcon,
    }


def _create_capturing_sink(com: dict[str, object]) -> tuple[object, object]:
    """Create an IFileOperationProgressSink that records PostDeleteItem's new item."""
    pythoncom = com["pythoncom"]
    shell = com["shell"]
    shellcon = com["shellcon"]
    DesignatedWrapPolicy = com["DesignatedWrapPolicy"]

    class CapturingFileOperationProgressSink(DesignatedWrapPolicy):  # type: ignore[misc, valid-type]
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
            "ResetTimer",
            "PauseTimer",
            "ResumeTimer",
        ]

        def __init__(self) -> None:
            self._wrap_(self)
            self.new_item_path: str | None = None

        def StartOperations(self) -> int:
            return _S_OK

        def FinishOperations(self, result: int) -> int:
            return _S_OK

        def PreRenameItem(self, flags: int, item: object, new_name: str) -> int:
            return _S_OK

        def PostRenameItem(
            self, flags: int, item: object, new_name: str, hr: int, newly_created: object
        ) -> int:
            return _S_OK

        def PreMoveItem(
            self, flags: int, item: object, dest: object, new_name: str
        ) -> int:
            return _S_OK

        def PostMoveItem(
            self,
            flags: int,
            item: object,
            dest: object,
            new_name: str,
            hr: int,
            newly_created: object,
        ) -> int:
            return _S_OK

        def PreCopyItem(
            self, flags: int, item: object, dest: object, new_name: str
        ) -> int:
            return _S_OK

        def PostCopyItem(
            self,
            flags: int,
            item: object,
            dest: object,
            new_name: str,
            hr: int,
            newly_created: object,
        ) -> int:
            return _S_OK

        def PreDeleteItem(self, flags: int, item: object) -> int:
            # Refuse operations that would permanently delete instead of recycling.
            recycle_flag = getattr(
                shellcon, "TSF_DELETE_RECYCLE_IF_POSSIBLE", _TSF_DELETE_RECYCLE_IF_POSSIBLE
            )
            return _S_OK if flags & recycle_flag else _E_FAIL

        def PostDeleteItem(
            self, flags: int, item: object, hr_delete: int, newly_created: object
        ) -> int:
            if newly_created is not None:
                shgdn = getattr(shellcon, "SHGDN_FORPARSING", 0x8000)
                self.new_item_path = newly_created.GetDisplayName(shgdn)
            return _S_OK

        def PreNewItem(self, flags: int, dest: object, new_name: str) -> int:
            return _S_OK

        def PostNewItem(
            self,
            flags: int,
            dest: object,
            new_name: str,
            template: str,
            file_attrs: int,
            hr: int,
            newly_created: object,
        ) -> int:
            return _S_OK

        def UpdateProgress(self, work_total: int, work_so_far: int) -> int:
            return _S_OK

        def ResetTimer(self) -> int:
            return _S_OK

        def PauseTimer(self) -> int:
            return _S_OK

        def ResumeTimer(self) -> int:
            return _S_OK

    sink_obj = CapturingFileOperationProgressSink()
    wrapped = pythoncom.WrapObject(sink_obj, shell.IID_IFileOperationProgressSink)
    return sink_obj, wrapped


def send_to_recycle_bin(
    path: Path | str,
    *,
    com_loader: Callable[[], dict[str, object]] | None = None,
) -> Path:
    """Move *path* to the Windows Recycle Bin via ``IFileOperation`` and return the ``$R`` path.

    Uses recycle/undo Shell flags and a progress sink that captures
    ``PostDeleteItem``'s newly created item parsing path. Never permanently
    deletes: ``PreDeleteItem`` fails the operation when recycle is not possible.

    Raises:
        OSError: COM missing, operation failed/aborted, or no exact recycled path.
    """
    source = Path(path)
    if not source.exists():
        raise OSError(f"source file does not exist: {source}")

    load = com_loader or _load_com_modules
    com = load()
    pythoncom = com["pythoncom"]
    pywintypes = com["pywintypes"]
    shell = com["shell"]
    shellcon = com["shellcon"]

    abs_path = str(source.resolve())
    abs_path = _strip_extended_path_prefix(abs_path)

    pythoncom.CoInitialize()
    try:
        fileop = pythoncom.CoCreateInstance(
            shell.CLSID_FileOperation,
            None,
            pythoncom.CLSCTX_ALL,
            shell.IID_IFileOperation,
        )
        flags = (
            _FOF_NOCONFIRMATION
            | _FOF_NOERRORUI
            | _FOF_SILENT
            | _FOFX_EARLYFAILURE
            | _FOFX_ADDUNDORECORD
            | _FOFX_RECYCLEONDELETE
            | _FOF_ALLOWUNDO
        )
        # Prefer named shellcon constants when present (Windows 8+ values above).
        for name, value in (
            ("FOF_NOCONFIRMATION", _FOF_NOCONFIRMATION),
            ("FOF_NOERRORUI", _FOF_NOERRORUI),
            ("FOF_SILENT", _FOF_SILENT),
            ("FOFX_EARLYFAILURE", _FOFX_EARLYFAILURE),
            ("FOFX_ADDUNDORECORD", _FOFX_ADDUNDORECORD),
            ("FOFX_RECYCLEONDELETE", _FOFX_RECYCLEONDELETE),
            ("FOF_ALLOWUNDO", _FOF_ALLOWUNDO),
        ):
            flags |= int(getattr(shellcon, name, value))

        fileop.SetOperationFlags(flags)
        sink_obj, sink_wrapped = _create_capturing_sink(com)
        try:
            item = shell.SHCreateItemFromParsingName(abs_path, None, shell.IID_IShellItem)
            fileop.DeleteItem(item, sink_wrapped)
            result = fileop.PerformOperations()
            aborted = bool(fileop.GetAnyOperationsAborted())
        except pywintypes.com_error as error:  # type: ignore[attr-defined]
            strerror = getattr(error, "strerror", None) or str(error)
            hresult = getattr(error, "hresult", None)
            raise OSError(
                None,
                f"IFileOperation recycle failed: {strerror}",
                abs_path,
                hresult,
            ) from error

        if aborted:
            raise OSError(
                None,
                "IFileOperation recycle aborted; file was not recycled",
                abs_path,
            )
        if result:
            raise OSError(
                None,
                f"IFileOperation recycle failed with result {result}",
                abs_path,
                result if isinstance(result, int) else None,
            )

        new_path = getattr(sink_obj, "new_item_path", None)
        if not new_path:
            raise OSError(
                None,
                "IFileOperation did not supply an exact recycled path "
                "(PostDeleteItem newly-created item missing); cannot log undo destination",
                abs_path,
            )
        return Path(str(new_path))
    finally:
        pythoncom.CoUninitialize()
