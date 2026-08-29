"""Crash-safety regression tests using only fake trash and COM backends."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.controllers.library_controller import LibraryController
from app.paths import AppPaths
from engine.database.models import PhotoRecord
from engine.database.repository import PhotoRepository
from engine.delete.delete_service import DeleteService
from engine.delete.undo_delete_service import UndoDeleteService
from engine.duplicates.exact_duplicates import sha256_file
from engine.fsutil import atomic_write_text
from engine.rename.undo_service import UndoService


def _recording_trash(bin_dir: Path, calls: list[Path]):
    bin_dir.mkdir()

    def trash(source: Path) -> Path:
        calls.append(source)
        destination = bin_dir / f"{len(calls)}-{source.name}"
        source.rename(destination)
        return destination

    return trash


def test_log_failure_reports_trashed_and_batch_continues(tmp_path, monkeypatch):
    survivor = tmp_path / "keep.jpg"
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    for path in (survivor, first, second):
        path.write_bytes(b"same")
    calls: list[Path] = []
    service = DeleteService(
        tmp_path / "delete.jsonl", trash_fn=_recording_trash(tmp_path / "bin", calls)
    )
    original_write = service._write_log
    attempts = {"count": 0}

    def fail_once(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OSError("disk full")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(service, "_write_log", fail_once)
    from engine.delete.delete_service import DeleteGroup

    results = service.delete_groups(
        [DeleteGroup(survivor, sha256_file(survivor), (first, second))],
        roots=[tmp_path],
    )

    assert results[0].trashed and not results[0].undo_logged
    assert "Recycle Bin" in results[0].error
    assert results[1].trashed and results[1].undo_logged
    assert calls == [first, second]


def test_keyboard_interrupt_keeps_prior_catalog_update_and_current_log(tmp_path):
    paths = [tmp_path / name for name in ("keep.jpg", "first.jpg", "second.jpg")]
    records = []
    repository = PhotoRepository(tmp_path / "catalog.sqlite3")
    for path in paths:
        path.write_bytes(b"same")
        record = PhotoRecord(
            str(path), sha256_file(path), 4, duplicate_group="group-1"
        )
        repository.insert(record)
        records.append(record)
    controller = LibraryController(repository, AppPaths.from_root(tmp_path / "data"))
    controller.set_roots([tmp_path])
    keep_id = repository.get_by_path(paths[0]).id
    review = controller.delete_review(overrides={"group-1": keep_id})
    calls: list[Path] = []
    move = _recording_trash(tmp_path / "bin", calls)

    def interrupt_second(source: Path) -> Path:
        if calls:
            raise KeyboardInterrupt
        return move(source)

    current_log = controller.paths.undo_logs / "delete-current.jsonl"
    controller.last_delete_log = tmp_path / "old.jsonl"
    with pytest.raises(KeyboardInterrupt):
        controller.delete_duplicates(
            review, DeleteService(current_log, trash_fn=interrupt_second)
        )

    assert repository.get_by_path(paths[1]).status == "deleted"
    assert repository.get_by_path(paths[2]).status == "indexed"
    assert controller.last_delete_log == current_log
    # Paths are backslash-escaped inside the JSON, so parse rather than substring match.
    logged_paths = {
        json.loads(line)["original_path"]
        for line in current_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert str(paths[1].resolve()) in logged_paths
    assert any(record.path == str(paths[1]) and record.status == "deleted" for record in controller.records)
    repository.close()


def test_atomic_write_replace_failure_preserves_target_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / "log.jsonl"
    target.write_bytes(b"original bytes\n")

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr("engine.fsutil.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(target, "replacement\n")

    assert target.read_bytes() == b"original bytes\n"
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


@pytest.mark.parametrize(
    ("service_type", "entry"),
    [
        (
            UndoDeleteService,
            {"original_path": "missing.jpg", "trashed_to": "missing-bin.jpg"},
        ),
        (UndoService, {"source": "missing.jpg", "target": "missing-renamed.jpg"}),
    ],
)
def test_undo_log_survives_failed_atomic_rewrite(
    tmp_path, monkeypatch, service_type, entry
):
    log = tmp_path / "undo.jsonl"
    original = (json.dumps(entry) + "\n").encode()
    log.write_bytes(original)

    def fail_write(_path, _content):
        raise OSError("rewrite failed")

    module = (
        "engine.delete.undo_delete_service.atomic_write_text"
        if service_type is UndoDeleteService
        else "engine.rename.undo_service.atomic_write_text"
    )
    monkeypatch.setattr(module, fail_write)
    kwargs = {"enable_default_legacy_locator": False} if service_type is UndoDeleteService else {}
    with pytest.raises(OSError, match="rewrite failed"):
        service_type(log, **kwargs).restore_all()

    assert log.read_bytes() == original
