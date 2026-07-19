"""Unit tests for Windows Recycle Bin helpers (no real COM or Recycle Bin)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from engine.delete.windows_recycle_bin import (
    SUPPORTED_I_HEADER_VERSIONS,
    build_i_file_bytes,
    locate_trashed_file,
    make_legacy_locator,
    normalize_windows_path,
    parse_i_file,
    r_path_for_i_path,
    send_to_recycle_bin,
)
from engine.duplicates.exact_duplicates import sha256_file


# ---------------------------------------------------------------------------
# $I metadata parsing
# ---------------------------------------------------------------------------


def test_parse_i_file_version_2_unicode_path():
    original = r"C:\Users\写真\album\vacation.jpg"
    blob = build_i_file_bytes(original, file_size=4096, version=2, deletion_filetime=123)
    meta = parse_i_file(blob)
    assert meta.version == 2
    assert meta.original_path == original
    assert meta.file_size == 4096
    assert meta.deletion_filetime == 123


def test_parse_i_file_version_1_fixed_path_field():
    original = r"D:\Photos\dup.png"
    blob = build_i_file_bytes(original, file_size=99, version=1, deletion_filetime=7)
    meta = parse_i_file(blob)
    assert meta.version == 1
    assert meta.original_path == original
    assert meta.file_size == 99


def test_parse_i_file_rejects_unknown_version():
    blob = build_i_file_bytes(r"C:\a.jpg", 1, version=2)
    # Corrupt header version to 99.
    corrupted = (99).to_bytes(8, "little") + blob[8:]
    with pytest.raises(ValueError, match="unsupported \\$I header version"):
        parse_i_file(corrupted)


def test_parse_i_file_rejects_truncated_v2():
    blob = build_i_file_bytes(r"C:\long\path\file.jpg", 10, version=2)
    with pytest.raises(ValueError, match="truncated"):
        parse_i_file(blob[:20])


def test_parse_i_file_rejects_truncated_v1():
    blob = build_i_file_bytes(r"C:\a.jpg", 10, version=1)
    with pytest.raises(ValueError, match="truncated"):
        parse_i_file(blob[:100])


def test_supported_i_header_versions_documented():
    assert SUPPORTED_I_HEADER_VERSIONS == frozenset({1, 2})


def test_r_path_for_i_path_mapping():
    i_path = Path(r"C:\$Recycle.Bin\S-1-5-21\$IABC123.jpg")
    assert r_path_for_i_path(i_path).name == "$RABC123.jpg"


def test_normalize_windows_path_case_and_slashes():
    assert normalize_windows_path(r"C:\Users\Foo\Bar.JPG") == normalize_windows_path(
        r"c:/users/foo/bar.jpg"
    )


# ---------------------------------------------------------------------------
# Legacy $I/$R locator (injected temporary root — never real Recycle Bin)
# ---------------------------------------------------------------------------


def _seed_recycle_pair(
    sid_dir: Path,
    *,
    original_path: str,
    content: bytes,
    version: int = 2,
    file_id: str = "ABC123",
    ext: str = ".jpg",
) -> Path:
    sid_dir.mkdir(parents=True, exist_ok=True)
    i_path = sid_dir / f"$I{file_id}{ext}"
    r_path = sid_dir / f"$R{file_id}{ext}"
    i_path.write_bytes(build_i_file_bytes(original_path, len(content), version=version))
    r_path.write_bytes(content)
    return r_path


def test_locate_trashed_file_exact_match(tmp_path):
    original = r"C:\Library\dup.jpg"
    content = b"exact-bytes"
    sid = tmp_path / "SID-1"
    expected_r = _seed_recycle_pair(sid, original_path=original, content=content)

    found = locate_trashed_file(
        original,
        expected_sha256=sha256_file(expected_r),
        source_size=len(content),
        recycle_bin_roots=[sid],
    )
    assert found == expected_r


def test_locate_trashed_file_hash_mismatch(tmp_path):
    original = r"C:\Library\dup.jpg"
    content = b"payload"
    sid = tmp_path / "SID-1"
    _seed_recycle_pair(sid, original_path=original, content=content)

    with pytest.raises(OSError, match="SHA-256"):
        locate_trashed_file(
            original,
            expected_sha256="0" * 64,
            source_size=len(content),
            recycle_bin_roots=[sid],
        )


def test_locate_trashed_file_ambiguous_path_size_match(tmp_path):
    original = r"C:\Library\dup.jpg"
    content = b"same"
    sid = tmp_path / "SID-1"
    _seed_recycle_pair(sid, original_path=original, content=content, file_id="ONE")
    _seed_recycle_pair(sid, original_path=original, content=content, file_id="TWO")

    with pytest.raises(OSError, match="ambiguous"):
        locate_trashed_file(
            original,
            source_size=len(content),
            recycle_bin_roots=[sid],
        )


def test_locate_trashed_file_zero_matches(tmp_path):
    sid = tmp_path / "SID-1"
    _seed_recycle_pair(
        sid,
        original_path=r"C:\Other\file.jpg",
        content=b"x",
    )
    with pytest.raises(OSError, match="no recycle-bin candidate"):
        locate_trashed_file(
            r"C:\Library\missing.jpg",
            source_size=1,
            recycle_bin_roots=[sid],
        )


def test_locate_trashed_file_size_filter(tmp_path):
    original = r"C:\Library\dup.jpg"
    sid = tmp_path / "SID-1"
    _seed_recycle_pair(sid, original_path=original, content=b"12345", file_id="A")
    with pytest.raises(OSError, match="no recycle-bin candidate"):
        locate_trashed_file(
            original,
            source_size=999,
            recycle_bin_roots=[sid],
        )


def test_make_legacy_locator_from_log_entry(tmp_path):
    original = r"C:\Library\dup.jpg"
    content = b"logged"
    sid = tmp_path / "SID-1"
    r_path = _seed_recycle_pair(sid, original_path=original, content=content)
    locator = make_legacy_locator(recycle_bin_roots=[sid])
    entry = {
        "original_path": original,
        "sha256": sha256_file(r_path),
        "source_size": len(content),
        "trashed_to": None,
    }
    assert locator(entry) == r_path


# ---------------------------------------------------------------------------
# IFileOperation path — fully mocked COM (never touches real Shell / Bin)
# ---------------------------------------------------------------------------


class _FakeShellItem:
    def GetDisplayName(self, _flags):
        return r"C:\$Recycle.Bin\S-1-5-21\$RFAKE001.jpg"


class _FakeFileOperation:
    def __init__(self, *, result=0, aborted=False, raise_com=False, capture_path=True):
        self._result = result
        self._aborted = aborted
        self._raise_com = raise_com
        self._capture_path = capture_path
        self._sink = None
        self.flags = None

    def SetOperationFlags(self, flags):
        self.flags = flags

    def DeleteItem(self, item, sink):
        self._sink = sink

    def PerformOperations(self):
        if self._raise_com:
            raise _FakeComError("access denied")
        return self._result

    def GetAnyOperationsAborted(self):
        return self._aborted


class _FakeComError(Exception):
    strerror = "access denied"
    hresult = -2147024891


def _fake_com_loader(
    *,
    result=0,
    aborted=False,
    raise_com=False,
    capture_path=True,
    recycled_path=r"C:\$Recycle.Bin\S-1-5-21\$RFAKE001.jpg",
):
    fileop = _FakeFileOperation(
        result=result, aborted=aborted, raise_com=raise_com, capture_path=capture_path
    )
    captured = {"sink_obj": None}

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
            return None

        @staticmethod
        def CoUninitialize():
            return None

        @staticmethod
        def CoCreateInstance(clsid, unk, ctx, iid):
            return fileop

        @staticmethod
        def WrapObject(obj, iid):
            captured["sink_obj"] = obj
            if capture_path:
                # Simulate PostDeleteItem writing the recycled path onto the sink.
                obj.new_item_path = recycled_path
            return obj

    class FakeDesignatedWrapPolicy:
        def _wrap_(self, obj):
            return None

    class FakePywintypes:
        com_error = _FakeComError

    def loader():
        return {
            "pythoncom": FakePythoncom,
            "pywintypes": FakePywintypes,
            "DesignatedWrapPolicy": FakeDesignatedWrapPolicy,
            "shell": FakeShell,
            "shellcon": FakeShellcon,
        }

    loader.fileop = fileop  # type: ignore[attr-defined]
    loader.captured = captured  # type: ignore[attr-defined]
    return loader


def test_send_to_recycle_bin_returns_exact_path_from_sink(tmp_path):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"data")
    loader = _fake_com_loader(recycled_path=r"C:\$Recycle.Bin\S-1\$RXYZ.jpg")

    result = send_to_recycle_bin(source, com_loader=loader)

    assert result == Path(r"C:\$Recycle.Bin\S-1\$RXYZ.jpg")
    assert loader.fileop.flags is not None
    # Recycle / undo flags must be present.
    assert loader.fileop.flags & 0x00080000  # FOFX_RECYCLEONDELETE
    assert loader.fileop.flags & 0x20000000  # FOFX_ADDUNDORECORD


def test_send_to_recycle_bin_raises_when_com_missing(tmp_path):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"data")

    def missing():
        raise OSError(
            "Windows Recycle Bin delete requires pywin32 (install "
            "'send2trash[nativeLib]' or 'pywin32>=305' on Windows)"
        )

    with pytest.raises(OSError, match="pywin32"):
        send_to_recycle_bin(source, com_loader=missing)


def test_send_to_recycle_bin_raises_on_com_failure(tmp_path):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"data")
    loader = _fake_com_loader(raise_com=True)

    with pytest.raises(OSError, match="IFileOperation recycle failed"):
        send_to_recycle_bin(source, com_loader=loader)


def test_send_to_recycle_bin_raises_when_aborted(tmp_path):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"data")
    loader = _fake_com_loader(aborted=True)

    with pytest.raises(OSError, match="aborted"):
        send_to_recycle_bin(source, com_loader=loader)


def test_send_to_recycle_bin_raises_when_no_exact_path(tmp_path):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"data")
    loader = _fake_com_loader(capture_path=False)

    with pytest.raises(OSError, match="exact recycled path"):
        send_to_recycle_bin(source, com_loader=loader)


def test_send_to_recycle_bin_raises_on_nonzero_result(tmp_path):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"data")
    loader = _fake_com_loader(result=5)

    with pytest.raises(OSError, match="result 5"):
        send_to_recycle_bin(source, com_loader=loader)
