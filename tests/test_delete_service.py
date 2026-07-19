import json
from pathlib import Path

from engine.delete.delete_service import (
    INJECTED_TRASH_BACKEND,
    WINDOWS_TRASH_BACKEND,
    DeleteService,
)
from engine.delete.undo_delete_service import UndoDeleteService
from engine.delete.windows_recycle_bin import build_i_file_bytes, make_legacy_locator
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
    assert entry["trashed"] is True
    assert entry["trash_backend"] == INJECTED_TRASH_BACKEND
    assert entry["source_size"] == len(b"duplicate")


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


def test_partial_batch_logs_and_restores_independently(tmp_path):
    good = tmp_path / "good.jpg"
    bad = tmp_path / "bad.jpg"
    good.write_bytes(b"keep-me")
    bad.write_bytes(b"stale")
    log = tmp_path / "delete.jsonl"
    stale_hash = "b" * 64

    results = DeleteService(log, trash_fn=fake_trash(tmp_path)).delete_paths(
        [(good, sha256_file(good)), (bad, stale_hash)]
    )

    assert results[0].trashed and results[1].trashed is False
    assert bad.exists()
    assert log.is_file()

    undo = UndoDeleteService(log).restore_all()
    assert len(undo) == 1 and undo[0].undone
    assert good.read_bytes() == b"keep-me"


def test_windows_backend_none_destination_is_failure_not_logged(tmp_path):
    target = tmp_path / "target.jpg"
    target.write_bytes(b"data")
    log = tmp_path / "delete.jsonl"

    def none_trash(_source: Path) -> None:
        return None

    result = DeleteService(
        log,
        trash_fn=none_trash,
        trash_backend=WINDOWS_TRASH_BACKEND,
    ).delete_paths([(target, sha256_file(target))])[0]

    assert not result.trashed
    assert "exact recycled path" in result.error
    assert not log.exists()
    assert target.exists()


def test_exact_path_logging_and_restore_round_trip(tmp_path):
    target = tmp_path / "photo.jpg"
    payload = b"round-trip-bytes"
    target.write_bytes(payload)
    digest = sha256_file(target)
    log = tmp_path / "delete.jsonl"
    trash_root = tmp_path / "bin"
    trash_root.mkdir()

    def trash_to_r(source: Path) -> Path:
        dest = trash_root / f"$R{source.name}"
        source.rename(dest)
        return dest

    deleted = DeleteService(log, trash_fn=trash_to_r).delete_paths([(target, digest)])[0]
    entry = json.loads(log.read_text(encoding="utf-8").strip())
    assert entry["trashed_to"] == str(deleted.trashed_to)
    assert entry["trashed"] is True
    assert entry["source_size"] == len(payload)
    assert entry["sha256"] == digest

    result = UndoDeleteService(log).restore_all()[0]
    assert result.undone
    assert target.read_bytes() == payload
    assert sha256_file(target) == digest
    assert log.read_text(encoding="utf-8") == ""


def test_old_schema_log_with_exact_path_still_restores(tmp_path):
    """Old log readers/writers without trashed/trash_backend/source_size remain usable."""
    original = tmp_path / "old.jpg"
    trash = tmp_path / "trash" / "0-old.jpg"
    trash.parent.mkdir(parents=True)
    trash.write_bytes(b"legacy-content")
    log = tmp_path / "delete.jsonl"
    old_entry = {
        "original_path": str(original),
        "sha256": sha256_file(trash),
        "trashed_to": str(trash),
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    log.write_text(json.dumps(old_entry) + "\n", encoding="utf-8")

    result = UndoDeleteService(log, enable_default_legacy_locator=False).restore_all()[0]

    assert result.undone
    assert original.read_bytes() == b"legacy-content"
    assert log.read_text(encoding="utf-8") == ""


def test_legacy_null_trashed_to_restores_via_injected_locator(tmp_path):
    original_path = r"C:\Photos\dup.jpg"
    content = b"from-bin"
    restore_target = tmp_path / "Photos" / "dup.jpg"
    sid = tmp_path / "$Recycle.Bin" / "S-1-5-21"
    sid.mkdir(parents=True)
    i_path = sid / "$ILEGACY01.jpg"
    r_path = sid / "$RLEGACY01.jpg"
    i_path.write_bytes(build_i_file_bytes(original_path, len(content), version=2))
    r_path.write_bytes(content)
    digest = sha256_file(r_path)

    log = tmp_path / "delete.jsonl"
    # Historical successful log: DeleteService only wrote after trash succeeded.
    entry = {
        "original_path": str(restore_target),
        "sha256": digest,
        "trashed_to": None,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "source_size": len(content),
    }
    # Locator matches on the path recorded in $I; point $I at restore_target for the test.
    i_path.write_bytes(build_i_file_bytes(str(restore_target), len(content), version=2))
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    locator = make_legacy_locator(recycle_bin_roots=[sid])
    result = UndoDeleteService(log, legacy_locator=locator).restore_all()[0]

    assert result.undone
    assert restore_target.read_bytes() == content
    assert log.read_text(encoding="utf-8") == ""


def test_legacy_null_trashed_to_ambiguous_left_in_log(tmp_path):
    restore_target = tmp_path / "dup.jpg"
    content = b"same"
    sid = tmp_path / "SID"
    sid.mkdir()
    for file_id in ("AAA", "BBB"):
        (sid / f"$I{file_id}.jpg").write_bytes(
            build_i_file_bytes(str(restore_target), len(content), version=1)
        )
        (sid / f"$R{file_id}.jpg").write_bytes(content)

    log = tmp_path / "delete.jsonl"
    entry = {
        "original_path": str(restore_target),
        "sha256": sha256_file(sid / "$RAAA.jpg"),
        "trashed_to": None,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "source_size": len(content),
    }
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    locator = make_legacy_locator(recycle_bin_roots=[sid])
    result = UndoDeleteService(log, legacy_locator=locator).restore_all()[0]

    assert not result.undone
    assert "ambiguous" in result.error
    assert log.read_text(encoding="utf-8").strip()
    assert not restore_target.exists()


def test_legacy_null_trashed_to_hash_mismatch_left_in_log(tmp_path):
    restore_target = tmp_path / "dup.jpg"
    content = b"payload"
    sid = tmp_path / "SID"
    sid.mkdir()
    (sid / "$IONLY.jpg").write_bytes(
        build_i_file_bytes(str(restore_target), len(content), version=2)
    )
    (sid / "$RONLY.jpg").write_bytes(content)

    log = tmp_path / "delete.jsonl"
    entry = {
        "original_path": str(restore_target),
        "sha256": "a" * 64,
        "trashed_to": None,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "source_size": len(content),
    }
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    locator = make_legacy_locator(recycle_bin_roots=[sid])
    result = UndoDeleteService(log, legacy_locator=locator).restore_all()[0]

    assert not result.undone
    assert "SHA-256" in result.error
    assert log.read_text(encoding="utf-8").strip()


def test_exact_path_restore_refuses_hash_mismatch(tmp_path):
    original = tmp_path / "out.jpg"
    trash = tmp_path / "trash" / "file.jpg"
    trash.parent.mkdir()
    trash.write_bytes(b"tampered")
    log = tmp_path / "delete.jsonl"
    entry = {
        "original_path": str(original),
        "sha256": "c" * 64,
        "trashed_to": str(trash),
        "timestamp": "2026-01-01T00:00:00+00:00",
        "trashed": True,
        "trash_backend": "injected",
        "source_size": 8,
    }
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = UndoDeleteService(log).restore_all()[0]

    assert not result.undone
    assert "SHA-256" in result.error
    assert trash.exists()
    assert not original.exists()
    assert log.read_text(encoding="utf-8").strip()


def test_delete_service_default_windows_platform_uses_windows_backend_name(tmp_path):
    """Injectable platform seam: no real COM; only backend labeling is checked."""
    target = tmp_path / "w.jpg"
    target.write_bytes(b"x")
    digest = sha256_file(target)
    DeleteService(
        tmp_path / "delete.jsonl",
        platform="nt",
        trash_fn=fake_trash(tmp_path),
        trash_backend=WINDOWS_TRASH_BACKEND,
    ).delete_paths([(target, digest)])
    entry = json.loads((tmp_path / "delete.jsonl").read_text(encoding="utf-8").strip())
    assert entry["trash_backend"] == WINDOWS_TRASH_BACKEND
