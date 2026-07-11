from pathlib import Path

import pytest

pytest.importorskip("PySide6", exc_type=ImportError)

from app.views.folder_panel import _aggregate_folder_counts
from engine.database.models import PhotoRecord


def test_folder_counts_are_aggregated_for_nested_roots(tmp_path: Path) -> None:
    library = tmp_path / "library"
    nested = library / "trip"
    nested.mkdir(parents=True)
    existing = nested / "photo.jpg"
    existing.write_bytes(b"photo")
    missing = nested / "missing.jpg"
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    records = [
        PhotoRecord(path=str(existing), sha256="a", size=5),
        PhotoRecord(path=str(missing), sha256="b", size=0, status="missing"),
        PhotoRecord(path=str(outside), sha256="c", size=7),
    ]

    counts = _aggregate_folder_counts(records, [library.resolve(), nested.resolve()])

    assert counts == [(2, 1), (2, 1)]
