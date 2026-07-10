"""SQLite persistence for the local photo catalog."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import PhotoRecord


class PhotoRepository:
    def __init__(self, database: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(database))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL UNIQUE,
                sha256 TEXT NOT NULL,
                size INTEGER NOT NULL,
                captured_at TEXT,
                width INTEGER,
                height INTEGER
            )
            """
        )
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_photos_sha256 ON photos(sha256)")
        self.connection.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> PhotoRecord | None:
        return PhotoRecord(**dict(row)) if row is not None else None

    def insert(self, photo: PhotoRecord) -> PhotoRecord:
        cursor = self.connection.execute(
            "INSERT INTO photos(path, sha256, size, captured_at, width, height) VALUES (?, ?, ?, ?, ?, ?)",
            (photo.path, photo.sha256, photo.size, photo.captured_at, photo.width, photo.height),
        )
        self.connection.commit()
        stored = self.get(cursor.lastrowid)
        assert stored is not None
        return stored

    def update(self, photo: PhotoRecord) -> PhotoRecord:
        if photo.id is None:
            raise ValueError("A record id is required for update")
        cursor = self.connection.execute(
            """UPDATE photos SET path = ?, sha256 = ?, size = ?, captured_at = ?, width = ?, height = ?
               WHERE id = ?""",
            (photo.path, photo.sha256, photo.size, photo.captured_at, photo.width, photo.height, photo.id),
        )
        if cursor.rowcount == 0:
            self.connection.rollback()
            raise KeyError(f"Photo record {photo.id} does not exist")
        self.connection.commit()
        stored = self.get(photo.id)
        assert stored is not None
        return stored

    def get(self, photo_id: int) -> PhotoRecord | None:
        row = self.connection.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        return self._from_row(row)

    def get_by_path(self, path: str | Path) -> PhotoRecord | None:
        row = self.connection.execute("SELECT * FROM photos WHERE path = ?", (str(path),)).fetchone()
        return self._from_row(row)

    def list_all(self) -> list[PhotoRecord]:
        rows = self.connection.execute("SELECT * FROM photos ORDER BY id").fetchall()
        return [PhotoRecord(**dict(row)) for row in rows]

    def search_paths(self, query: str) -> list[PhotoRecord]:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self.connection.execute(
            "SELECT * FROM photos WHERE path LIKE ? ESCAPE '\\' ORDER BY path COLLATE NOCASE",
            (f"%{escaped}%",),
        ).fetchall()
        return [PhotoRecord(**dict(row)) for row in rows]

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "PhotoRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

