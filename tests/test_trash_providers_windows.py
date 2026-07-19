import pytest

from engine.delete.trash_providers.windows import (
    WindowsTrashError,
    quarantine_fallback,
    trash_to_windows_bin,
)


def test_returns_native_recycle_path_when_sink_captures_one(tmp_path):
    target = tmp_path / "target.jpg"
    target.write_bytes(b"data")
    recycled = tmp_path / "recycled" / "target.jpg"

    def fake_call(path):
        assert path == target
        return recycled

    result = trash_to_windows_bin(target, tmp_path / "trash", call=fake_call)

    assert result == recycled


def test_falls_back_to_quarantine_when_pywin32_unavailable(tmp_path):
    target = tmp_path / "target.jpg"
    target.write_bytes(b"data")

    def failing_call(path):
        raise ImportError("pywin32 not installed")

    result = trash_to_windows_bin(target, tmp_path / "trash", call=failing_call)

    assert result.parent == tmp_path / "trash"
    assert result.read_bytes() == b"data"
    assert not target.exists()


def test_falls_back_to_quarantine_when_sink_never_fires_and_file_untouched(tmp_path):
    target = tmp_path / "target.jpg"
    target.write_bytes(b"data")

    def fake_call(path):
        # Simulates IFileOperation completing (e.g. a cancelled UAC
        # prompt) without ever deleting the source and without the sink
        # reporting a destination.
        return None

    result = trash_to_windows_bin(target, tmp_path / "trash", call=fake_call)

    assert result.read_bytes() == b"data"
    assert not target.exists()


def test_refuses_to_guess_when_file_vanished_without_a_reported_destination(tmp_path):
    target = tmp_path / "target.jpg"
    target.write_bytes(b"data")

    def fake_call(path):
        # Simulates IFileOperation actually recycling the file for real,
        # but the progress sink failing to capture PostDeleteItem.
        path.unlink()
        return None

    with pytest.raises(WindowsTrashError, match="never reported"):
        trash_to_windows_bin(target, tmp_path / "trash", call=fake_call)

    # The quarantine folder must not be silently populated with a guess.
    assert not (tmp_path / "trash").exists()


def test_quarantine_fallback_moves_file_and_avoids_name_collisions(tmp_path):
    target = tmp_path / "target.jpg"
    target.write_bytes(b"data")
    quarantine_dir = tmp_path / "trash"

    result = quarantine_fallback(target, quarantine_dir)

    assert result.exists()
    assert result.parent == quarantine_dir
    assert result.name.endswith("-target.jpg")
    assert result.read_bytes() == b"data"
    assert not target.exists()


def test_quarantine_fallback_creates_missing_directory(tmp_path):
    target = tmp_path / "target.jpg"
    target.write_bytes(b"data")
    quarantine_dir = tmp_path / "does" / "not" / "exist"

    result = quarantine_fallback(target, quarantine_dir)

    assert result.exists()
