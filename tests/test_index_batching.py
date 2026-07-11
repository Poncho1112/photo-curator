import os
import sqlite3

import pytest

from app.controllers.library_controller import LibraryController
from app.paths import AppPaths
from engine.database.models import PhotoRecord
from engine.database.repository import PhotoRepository


def make_controller(tmp_path):
    repository = PhotoRepository(tmp_path / "catalog.sqlite3")
    return LibraryController(repository, AppPaths.from_root(tmp_path / "data"))


def test_index_records_preserves_rows_missing_status_and_duplicate_groups(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    present = root / "present.jpg"
    duplicate = root / "duplicate.jpg"
    gone = root / "gone.jpg"
    controller = make_controller(tmp_path)
    controller.repository.insert(PhotoRecord(str(present), "a" * 64, 1))
    controller.repository.insert(PhotoRecord(str(gone), "b" * 64, 2))

    indexed = controller.index_records(
        [
            PhotoRecord(str(present), "c" * 64, 3),
            PhotoRecord(str(duplicate), "c" * 64, 4),
        ],
        [root],
    )

    assert {record.path for record in indexed} == {str(present), str(duplicate), str(gone)}
    assert controller.repository.get_by_path(gone).status == "missing"
    assert controller.repository.get_by_path(present).sha256 == "c" * 64
    assert controller.repository.get_by_path(present).duplicate_group == "c" * 8
    assert controller.repository.get_by_path(duplicate).duplicate_group == "c" * 8
    controller.repository.close()


def test_batch_exception_rolls_back_and_connection_remains_usable(tmp_path):
    repository = PhotoRepository(tmp_path / "catalog.sqlite3")
    original = repository.insert(PhotoRecord("original.jpg", "a" * 64, 1))

    with pytest.raises(sqlite3.IntegrityError):
        with repository.batch():
            repository.insert(PhotoRecord("new.jpg", "b" * 64, 2))
            repository.insert(PhotoRecord("original.jpg", "c" * 64, 3))

    assert repository.get_by_path("new.jpg") is None
    assert repository.get(original.id).sha256 == "a" * 64
    stored = repository.insert(PhotoRecord("after.jpg", "d" * 64, 4))
    assert repository.get(stored.id).path == "after.jpg"
    repository.close()


def test_mark_missing_except_handles_nested_roots_and_path_boundaries(tmp_path):
    root = tmp_path / "photos"
    nested = root / "nested"
    sibling_prefix = tmp_path / "photos-other"
    nested.mkdir(parents=True)
    sibling_prefix.mkdir()
    keep = nested / "keep.jpg"
    missing = nested / "missing.jpg"
    outside = sibling_prefix / "outside.jpg"
    repository = PhotoRepository(tmp_path / "catalog.sqlite3")
    for path in (keep, missing, outside):
        repository.insert(PhotoRecord(str(path), path.name * 8, 1))

    repository.mark_missing_except({str(keep)}, [str(root) + os.sep + ".", str(nested)])

    assert repository.get_by_path(keep).status == "indexed"
    assert repository.get_by_path(missing).status == "missing"
    assert repository.get_by_path(outside).status == "indexed"
    repository.close()


def test_index_records_commits_once_for_multiple_writes(tmp_path):
    controller = make_controller(tmp_path)
    statements = []
    controller.repository.connection.set_trace_callback(statements.append)
    root = tmp_path / "photos"
    root.mkdir()

    controller.index_records(
        [PhotoRecord(str(root / f"{index}.jpg"), "a" * 64, index) for index in range(8)],
        [root],
    )

    commits = [statement for statement in statements if statement.strip().upper() == "COMMIT"]
    assert len(commits) == 1
    controller.repository.close()
