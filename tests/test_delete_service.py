import json
from pathlib import Path

from engine.delete.delete_service import DeleteService
from engine.delete.undo_delete_service import UndoDeleteService
from engine.duplicates.exact_duplicates import sha256_file


def fake_trash(tmp_path):
    trash = tmp_path / "trash"
    trash.mkdir()
    sequence = iter(range(1000))

    def move(source: Path) -> Path:
        destination = trash / f"{next(sequence)}-{source.name}"
        source.rename(destination)
        return destination

    return move


def test_delete_trashes_only_target_and_writes_destination_to_log(tmp_path):
    target = tmp_path / "target.jpg"
    untouched = tmp_path / "untouched.jpg"
    target.write_bytes(b"duplicate")
    untouched.write_bytes(b"other")
    log = tmp_path / "delete.jsonl"

    result = DeleteService(log, trash_fn=fake_trash(tmp_path)).delete_paths(
        [(target, sha256_file(target))]
    )[0]

    assert result.trashed
    assert result.trashed_to is not None and result.trashed_to.read_bytes() == b"duplicate"
    assert not target.exists()
    assert untouched.read_bytes() == b"other"
    entry = json.loads(log.read_text(encoding="utf-8").strip())
    assert entry["original_path"] == str(target.resolve())
    assert entry["trashed_to"] == str(result.trashed_to)
    assert entry["sha256"] == sha256_file(result.trashed_to)
    assert entry["timestamp"]


def test_changed_file_is_never_trashed_on_stale_hash(tmp_path):
    target = tmp_path / "changed.jpg"
    target.write_bytes(b"indexed bytes")
    indexed_sha256 = sha256_file(target)
    target.write_bytes(b"changed bytes")

    result = DeleteService(
        tmp_path / "delete.jsonl",
        trash_fn=fake_trash(tmp_path),
    ).delete_paths([(target, indexed_sha256)])[0]

    assert not result.trashed
    assert "changed" in result.error
    assert target.read_bytes() == b"changed bytes"
    assert not (tmp_path / "delete.jsonl").exists()


def test_missing_source_is_reported_cleanly(tmp_path):
    missing = tmp_path / "missing.jpg"
    result = DeleteService(
        tmp_path / "delete.jsonl",
        trash_fn=fake_trash(tmp_path),
    ).delete_paths([(missing, "a" * 64)])[0]

    assert not result.trashed
    assert result.error == "source file does not exist"


def test_trash_oserror_is_reported_without_writing_a_log(tmp_path):
    target = tmp_path / "target.jpg"
    target.write_bytes(b"duplicate")
    log = tmp_path / "delete.jsonl"

    def failing_trash(_source: Path) -> None:
        raise OSError("trash unavailable")

    result = DeleteService(log, trash_fn=failing_trash).delete_paths(
        [(target, sha256_file(target))]
    )[0]

    assert not result.trashed
    assert result.error == "trash unavailable"
    assert target.read_bytes() == b"duplicate"
    assert not log.exists()


def test_undo_restores_every_trashed_file(tmp_path):
    first, second = tmp_path / "first.jpg", tmp_path / "second.jpg"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    log = tmp_path / "delete.jsonl"
    DeleteService(log, trash_fn=fake_trash(tmp_path)).delete_paths(
        [(first, sha256_file(first)), (second, sha256_file(second))]
    )

    results = UndoDeleteService(log).restore_all()

    assert len(results) == 2 and all(result.undone for result in results)
    assert first.read_bytes() == second.read_bytes() == b"same"
    assert log.read_text(encoding="utf-8") == ""


def test_undo_refuses_to_overwrite_recreated_original(tmp_path):
    original = tmp_path / "original.jpg"
    original.write_bytes(b"trashed")
    log = tmp_path / "delete.jsonl"
    trashed = DeleteService(log, trash_fn=fake_trash(tmp_path)).delete_paths(
        [(original, sha256_file(original))]
    )[0]
    original.write_bytes(b"replacement")

    result = UndoDeleteService(log).restore_all()[0]

    assert not result.undone
    assert "overwrite refused" in result.error
    assert original.read_bytes() == b"replacement"
    assert trashed.trashed_to is not None and trashed.trashed_to.read_bytes() == b"trashed"
    assert log.read_text(encoding="utf-8").strip()
