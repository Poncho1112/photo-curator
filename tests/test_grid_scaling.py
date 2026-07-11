"""Scaling coverage for deferred PhotoGrid tile creation."""

from __future__ import annotations

import pytest


pytest.importorskip("PySide6", exc_type=ImportError)
from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from app.views.photo_grid import PhotoGrid, TILE_CREATION_CHUNK_SIZE
from app.widgets.photo_tile import PhotoTile
from engine.database.models import PhotoRecord


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _records(count: int, *, first_id: int = 1) -> list[PhotoRecord]:
    return [
        PhotoRecord(f"/missing/photo-{photo_id}.jpg", f"{photo_id:064x}"[-64:], 0, id=photo_id, status="missing")
        for photo_id in range(first_id, first_id + count)
    ]


def _drain_tiles(grid: PhotoGrid, app: QApplication, limit: int = 100) -> None:
    for _ in range(limit):
        if all(grid.itemWidget(grid.item(row)) is not None for row in range(grid.count())):
            return
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents)
    pytest.fail("deferred tile creation did not finish within the event-loop bound")


def test_large_population_defers_most_tile_construction(qt_app, monkeypatch):
    constructions = 0
    original_init = PhotoTile.__init__

    def counting_init(self, *args, **kwargs):
        nonlocal constructions
        constructions += 1
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(PhotoTile, "__init__", counting_init)
    grid = PhotoGrid()
    grid.populate(_records(3_000), set())

    assert grid.count() == 3_000
    assert constructions == TILE_CREATION_CHUNK_SIZE
    assert constructions < 1_000


def test_deferred_chunks_eventually_materialize_every_tile(qt_app):
    grid = PhotoGrid()
    grid.populate(_records(3_000), set())
    _drain_tiles(grid, qt_app)

    assert all(isinstance(grid.itemWidget(grid.item(row)), PhotoTile) for row in range(grid.count()))


def test_thumbnail_is_applied_when_pending_tile_materializes(tmp_path, qt_app):
    thumbnail = tmp_path / "thumbnail.png"
    pixmap = QPixmap(8, 8)
    pixmap.fill()
    assert pixmap.save(str(thumbnail))
    grid = PhotoGrid()
    target_id = TILE_CREATION_CHUNK_SIZE + 1
    grid.populate(_records(target_id), set())
    target_item = grid.item_for_id(target_id)
    assert grid.itemWidget(target_item) is None

    grid.set_thumbnail(target_id, str(thumbnail))
    _drain_tiles(grid, qt_app)

    target_tile = grid.itemWidget(target_item)
    assert isinstance(target_tile, PhotoTile)
    assert target_tile.image.pixmap() is not None
    assert not target_tile.image.pixmap().isNull()


def test_repopulate_discards_pending_tiles_from_old_population(qt_app):
    grid = PhotoGrid()
    grid.populate(_records(3_000), set())
    new_records = _records(7, first_id=10_001)

    grid.populate(new_records, set())
    _drain_tiles(grid, qt_app)

    assert grid.count() == len(new_records)
    assert [grid.item(row).data(Qt.ItemDataRole.UserRole) for row in range(grid.count())] == [
        record.id for record in new_records
    ]
    assert all(grid.itemWidget(grid.item(row)).photo_id == new_records[row].id for row in range(grid.count()))


def test_selection_and_rename_contract_survives_deferred_population(qt_app):
    grid = PhotoGrid()
    rename_ids = {2, TILE_CREATION_CHUNK_SIZE + 3}
    grid.populate(_records(TILE_CREATION_CHUNK_SIZE + 10), rename_ids)
    grid.item(0).setSelected(True)
    grid.item(TILE_CREATION_CHUNK_SIZE + 2).setSelected(True)

    assert grid.selected_photo_ids() == {1, TILE_CREATION_CHUNK_SIZE + 3}
    grid.populate(_records(TILE_CREATION_CHUNK_SIZE + 10), rename_ids)
    assert grid.selected_photo_ids() == {1, TILE_CREATION_CHUNK_SIZE + 3}
    _drain_tiles(grid, qt_app)

    checked_ids = {
        grid.itemWidget(grid.item(row)).photo_id
        for row in range(grid.count())
        if grid.itemWidget(grid.item(row)).rename_checkbox.isChecked()
    }
    assert checked_ids == rename_ids
    assert grid.selected_photo_ids() == {1, TILE_CREATION_CHUNK_SIZE + 3}
