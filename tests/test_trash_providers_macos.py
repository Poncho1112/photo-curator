import pytest

from engine.delete.trash_providers.macos import MacOSTrashError, trash_to_macos_trash


def test_trash_to_macos_trash_returns_resulting_path(tmp_path):
    source = tmp_path / "source.jpg"
    resulting = tmp_path / ".Trash" / "source.jpg"

    def fake_call(path):
        assert path == source
        return True, resulting, None

    result = trash_to_macos_trash(source, call=fake_call)

    assert result == resulting


def test_trash_to_macos_trash_raises_on_reported_failure(tmp_path):
    def fake_call(path):
        return False, None, "Operation not permitted"

    with pytest.raises(MacOSTrashError, match="Operation not permitted"):
        trash_to_macos_trash(tmp_path / "source.jpg", call=fake_call)


def test_trash_to_macos_trash_raises_when_success_but_no_destination(tmp_path):
    # Defensive case: NSFileManager reports success without a resulting
    # URL. Should not occur in practice, but must never be treated as a
    # silent, unrecorded deletion.
    def fake_call(path):
        return True, None, None

    with pytest.raises(MacOSTrashError, match="unknown error"):
        trash_to_macos_trash(tmp_path / "source.jpg", call=fake_call)


def test_default_backend_raises_actionable_import_error_without_pyobjc(tmp_path):
    # This sandbox has no pyobjc installed, so the real backend must
    # surface a clear, actionable ImportError rather than crashing with
    # a bare ModuleNotFoundError.
    with pytest.raises(ImportError, match="pyobjc-framework-Cocoa"):
        trash_to_macos_trash(tmp_path / "source.jpg")
