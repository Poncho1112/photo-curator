from dataclasses import replace
from pathlib import Path

import pytest

from app.controllers.library_controller import LibraryController
from app.paths import AppPaths
from engine.database.models import PhotoRecord
from engine.database.repository import PhotoRepository
from engine.delete.delete_service import DeleteService
from engine.duplicates.exact_duplicates import sha256_file


def make_controller(tmp_path, records):
    repository = PhotoRepository(tmp_path / "catalog.sqlite3")
    for record in records:
        repository.insert(record)
    return LibraryController(repository, AppPaths.from_root(tmp_path / "data"))


def fake_trash(tmp_path):
    trash = tmp_path / "trash"
    trash.mkdir(exist_ok=True)
    sequence = iter(range(1000))

    def move(source: Path) -> Path:
        destination = trash / f"{next(sequence)}-{source.name}"
        source.rename(destination)
        return destination

    return move


def duplicate_records(paths, content=b"duplicate", group="group-1"):
    records = []
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        records.append(PhotoRecord(str(path), sha256_file(path), len(content), duplicate_group=group))
    return records


def test_delete_review_keeps_exactly_one_and_never_targets_survivor(tmp_path):
    paths = [tmp_path / "a.jpg", tmp_path / "long" / "b.jpg", tmp_path / "longer" / "c.jpg"]
    controller = make_controller(tmp_path, duplicate_records(paths))

    review = controller.delete_review()

    assert len(review) == 1
    item = review[0]
    assert len(item.to_delete) == len(paths) - 1
    assert item.survivor not in item.to_delete
    assert {record.path for record in item.to_delete} | {item.survivor.path} == {
        str(path) for path in paths
    }
    controller.repository.close()


def test_executor_rejects_a_review_that_targets_its_survivor(tmp_path):
    paths = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    controller = make_controller(tmp_path, duplicate_records(paths))
    item = controller.delete_review()[0]
    unsafe = replace(item, to_delete=item.to_delete + (item.survivor,))

    with pytest.raises(ValueError, match="survivor"):
        controller.delete_duplicates([unsafe])

    assert all(path.exists() for path in paths)
    controller.repository.close()


def test_executor_rejects_forged_survivor_that_would_delete_last_copy(tmp_path):
    paths = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    controller = make_controller(tmp_path, duplicate_records(paths))
    item = controller.delete_review()[0]
    outsider = PhotoRecord(str(tmp_path / "outsider.jpg"), item.survivor.sha256, 1, id=999)
    unsafe = replace(
        item,
        survivor=outsider,
        to_delete=(item.survivor, *item.to_delete),
    )

    with pytest.raises(ValueError, match="survivor"):
        controller.delete_duplicates([unsafe])

    assert all(path.exists() for path in paths)
    controller.repository.close()


def test_delete_updates_only_non_survivors_and_undo_restores_them(tmp_path):
    paths = [tmp_path / "keep.jpg", tmp_path / "copies" / "delete.jpg"]
    controller = make_controller(tmp_path, duplicate_records(paths))
    keep_id = controller.repository.get_by_path(paths[0]).id
    review = controller.delete_review(overrides={"group-1": keep_id})
    log = controller.paths.undo_logs / "delete-test.jsonl"

    results = controller.delete_duplicates(
        review,
        DeleteService(log, trash_fn=fake_trash(tmp_path)),
    )

    assert len(results) == 1 and results[0].trashed
    assert paths[0].read_bytes() == b"duplicate"
    assert not paths[1].exists()
    assert controller.repository.get_by_path(paths[0]).status == "indexed"
    assert controller.repository.get_by_path(paths[1]).status == "deleted"

    restored = controller.undo_delete()
    assert len(restored) == 1 and restored[0].undone
    assert paths[1].read_bytes() == b"duplicate"
    assert controller.repository.get_by_path(paths[1]).status == "indexed"
    controller.repository.close()


def test_changed_duplicate_is_skipped_end_to_end(tmp_path):
    paths = [tmp_path / "keep.jpg", tmp_path / "delete.jpg"]
    controller = make_controller(tmp_path, duplicate_records(paths))
    keep_id = controller.repository.get_by_path(paths[0]).id
    review = controller.delete_review(overrides={"group-1": keep_id})
    paths[1].write_bytes(b"changed after review")

    results = controller.delete_duplicates(
        review,
        DeleteService(
            controller.paths.undo_logs / "delete-changed.jsonl",
            trash_fn=fake_trash(tmp_path),
        ),
    )

    assert len(results) == 1 and not results[0].trashed
    assert "changed" in results[0].error
    assert paths[0].read_bytes() == b"duplicate"
    assert paths[1].read_bytes() == b"changed after review"
    assert controller.repository.get_by_path(paths[1]).status == "indexed"
    controller.repository.close()
