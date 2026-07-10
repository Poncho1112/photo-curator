import sqlite3

import pytest

from engine.database.models import PhotoRecord
from engine.database.repository import PhotoRepository
from engine.search.search_service import SearchService


def test_insert_and_retrieve_record_in_temporary_database(tmp_path):
    database = tmp_path / "catalog.sqlite3"
    with PhotoRepository(database) as repository:
        stored = repository.insert(PhotoRecord("/photos/a.jpg", "a" * 64, 123, "2026-07-10", 800, 600))
        assert stored.id is not None
        assert repository.get(stored.id) == stored
        assert repository.get_by_path("/photos/a.jpg") == stored


def test_update_existing_record(tmp_path):
    with PhotoRepository(tmp_path / "catalog.sqlite3") as repository:
        stored = repository.insert(PhotoRecord("old.jpg", "a" * 64, 10))
        stored.path = "new.jpg"
        stored.size = 20
        updated = repository.update(stored)
        assert updated.path == "new.jpg"
        assert updated.size == 20
        assert repository.get_by_path("old.jpg") is None


def test_duplicate_paths_are_rejected(tmp_path):
    with PhotoRepository(tmp_path / "catalog.sqlite3") as repository:
        repository.insert(PhotoRecord("same.jpg", "a" * 64, 10))
        with pytest.raises(sqlite3.IntegrityError):
            repository.insert(PhotoRecord("same.jpg", "b" * 64, 20))


def test_search_service_finds_path_case_insensitively(tmp_path):
    with PhotoRepository(tmp_path / "catalog.sqlite3") as repository:
        wanted = repository.insert(PhotoRecord("/photos/Family/Beach.JPG", "a" * 64, 10))
        repository.insert(PhotoRecord("/photos/work.png", "b" * 64, 20))
        assert SearchService(repository).search("beach") == [wanted]

