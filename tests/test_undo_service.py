from datetime import date

from engine.rename.rename_service import RenameService
from engine.rename.undo_service import UndoService


def test_restore_filename_from_undo_log(tmp_path):
    original = tmp_path / "original.jpg"
    original.write_bytes(b"photo")
    log = tmp_path / "undo.jsonl"
    renamed = RenameService(log, date_provider=lambda _: date(2026, 7, 10)).rename_selected([original])[0]
    assert renamed.target is not None

    result = UndoService(log).restore_all()
    assert len(result) == 1 and result[0].undone
    assert original.read_bytes() == b"photo"
    assert not renamed.target.exists()
    assert log.read_text(encoding="utf-8") == ""


def test_undo_refuses_to_overwrite_restored_target(tmp_path):
    original = tmp_path / "original.jpg"
    original.write_bytes(b"photo")
    log = tmp_path / "undo.jsonl"
    renamed = RenameService(log, date_provider=lambda _: date(2026, 7, 10)).rename_selected([original])[0]
    original.write_bytes(b"replacement")

    result = UndoService(log).restore_all()[0]
    assert not result.undone
    assert "overwrite refused" in result.error
    assert original.read_bytes() == b"replacement"
    assert renamed.target is not None and renamed.target.read_bytes() == b"photo"
    assert log.read_text(encoding="utf-8").strip()


def test_undo_handles_missing_renamed_file(tmp_path):
    original = tmp_path / "original.jpg"
    original.write_bytes(b"photo")
    log = tmp_path / "undo.jsonl"
    renamed = RenameService(log, date_provider=lambda _: date(2026, 7, 10)).rename_selected([original])[0]
    assert renamed.target is not None
    renamed.target.unlink()

    result = UndoService(log).restore_all()[0]
    assert not result.undone
    assert result.error == "renamed file does not exist"

