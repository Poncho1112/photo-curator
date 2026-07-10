import csv
from pathlib import Path

from app.controllers.library_controller import LibraryController
from app.paths import AppPaths
from engine.database.models import PhotoRecord
from engine.database.repository import PhotoRepository


def make_controller(tmp_path, records=()):
    repository = PhotoRepository(tmp_path / "catalog.sqlite3")
    for record in records:
        repository.insert(record)
    return LibraryController(repository, AppPaths.from_root(tmp_path / "data"))


def test_controller_loads_previously_indexed_records(tmp_path):
    controller = make_controller(tmp_path, [PhotoRecord("one.jpg", "a" * 64, 1)])
    assert [record.path for record in controller.records] == ["one.jpg"]
    controller.repository.close()


def test_search_and_filter_state(tmp_path):
    present = tmp_path / "family-beach.jpg"
    present.write_bytes(b"photo")
    records = [
        PhotoRecord(str(present), "a" * 64, 5, duplicate_group="aaaa1111"),
        PhotoRecord(str(tmp_path / "work.jpg"), "b" * 64, 1, status="missing"),
    ]
    controller = make_controller(tmp_path, records)
    assert [record.path for record in controller.set_filters(query="beach")] == [str(present)]
    assert [record.path for record in controller.set_filters(query="", missing_only=True)] == [str(tmp_path / "work.jpg")]
    controller.repository.close()


def test_rename_selection_persists_when_filter_changes(tmp_path):
    controller = make_controller(tmp_path, [PhotoRecord("one.jpg", "a" * 64, 1), PhotoRecord("two.jpg", "b" * 64, 1)])
    selected_id = controller.records[0].id
    assert selected_id is not None
    controller.set_selected_for_rename(selected_id, True)
    controller.set_filters(query="two")
    assert selected_id in controller.rename_selection
    controller.set_filters(query="", selected_only=True)
    assert [record.id for record in controller.filtered_records()] == [selected_id]
    controller.repository.close()


def test_rename_review_detects_conflict_and_missing_source(tmp_path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")
    conflict = tmp_path / "proposed.jpg"
    conflict.write_bytes(b"existing")
    missing = tmp_path / "missing.jpg"
    controller = make_controller(tmp_path, [
        PhotoRecord(str(source), "a" * 64, 6, proposed_name=conflict.name),
        PhotoRecord(str(missing), "b" * 64, 1, proposed_name="other.jpg"),
    ])
    for record in controller.records:
        controller.set_selected_for_rename(record.id, True)
    review = controller.rename_review()
    assert review[0].conflict and not review[0].safe
    assert review[1].missing and not review[1].safe
    controller.repository.close()


def test_manifest_generation_does_not_modify_files(tmp_path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"photo")
    controller = make_controller(tmp_path, [PhotoRecord(str(source), "a" * 64, 5, "2026-01-02", proposed_name="new.jpg")])
    controller.set_selected_for_rename(controller.records[0].id, True)
    target = controller.export_manifest(tmp_path / "manifest.csv")
    rows = list(csv.DictReader(target.open(encoding="utf-8-sig")))
    assert rows[0]["current_path"] == str(source)
    assert rows[0]["proposed_path"] == str(tmp_path / "new.jpg")
    assert rows[0]["rename_status"] == "ready"
    assert source.read_bytes() == b"photo"
    controller.repository.close()


def test_index_updates_changed_records_and_marks_missing(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    present = root / "present.jpg"
    present.write_bytes(b"new")
    missing = root / "gone.jpg"
    controller = make_controller(tmp_path, [
        PhotoRecord(str(present), "a" * 64, 1), PhotoRecord(str(missing), "b" * 64, 1),
    ])
    controller.index_records([PhotoRecord(str(present), "c" * 64, 3)], [root])
    assert controller.repository.get_by_path(present).sha256 == "c" * 64
    assert controller.repository.get_by_path(missing).status == "missing"
    controller.repository.close()


def test_cancelled_index_does_not_mark_unprocessed_records_missing(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    first, second = root / "first.jpg", root / "second.jpg"
    controller = make_controller(tmp_path, [
        PhotoRecord(str(first), "a" * 64, 1), PhotoRecord(str(second), "b" * 64, 1),
    ])
    controller.index_records([PhotoRecord(str(first), "a" * 64, 1)], [root], complete_scan=False)
    assert controller.repository.get_by_path(second).status == "indexed"
    controller.repository.close()
