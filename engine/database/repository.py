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

    _COLUMNS = (
        "path", "sha256", "size", "captured_at", "width", "height", "modified_at",
        "proposed_name", "date_source", "camera_make", "camera_model", "status",
        "duplicate_group", "renamed_from",
    )

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
        additions = {
            "modified_at": "REAL", "proposed_name": "TEXT", "date_source": "TEXT",
            "camera_make": "TEXT", "camera_model": "TEXT", "status": "TEXT NOT NULL DEFAULT 'indexed'",
            "duplicate_group": "TEXT", "renamed_from": "TEXT",
        }
        existing = {row[1] for row in self.connection.execute("PRAGMA table_info(photos)")}
        for name, definition in additions.items():
            if name not in existing:
                self.connection.execute(f"ALTER TABLE photos ADD COLUMN {name} {definition}")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_photos_sha256 ON photos(sha256)")
        self.connection.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> PhotoRecord | None:
        return PhotoRecord(**dict(row)) if row is not None else None

    def insert(self, photo: PhotoRecord) -> PhotoRecord:
        placeholders = ", ".join("?" for _ in self._COLUMNS)
        cursor = self.connection.execute(
            f"INSERT INTO photos({', '.join(self._COLUMNS)}) VALUES ({placeholders})",
            tuple(getattr(photo, column) for column in self._COLUMNS),
        )
        self.connection.commit()
        stored = self.get(cursor.lastrowid)
        assert stored is not None
        return stored

    def update(self, photo: PhotoRecord) -> PhotoRecord:
        if photo.id is None:
            raise ValueError("A record id is required for update")
        assignments = ", ".join(f"{column} = ?" for column in self._COLUMNS)
        cursor = self.connection.execute(
            f"UPDATE photos SET {assignments} WHERE id = ?",
            (*tuple(getattr(photo, column) for column in self._COLUMNS), photo.id),
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

    def upsert_by_path(self, photo: PhotoRecord) -> PhotoRecord:
        existing = self.get_by_path(photo.path)
        if existing is None:
            return self.insert(photo)
        photo.id = existing.id
        return self.update(photo)

    def mark_missing_except(self, existing_paths: set[str], roots: list[str]) -> None:
        for photo in self.list_all():
            belongs = any(_is_beneath(photo.path, root) for root in roots)
            if belongs and photo.path not in existing_paths and photo.status != "missing":
                photo.status = "missing"
                self.update(photo)

    def search_paths(self, query: str) -> list[PhotoRecord]:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        fields = ("path", "proposed_name", "captured_at", "camera_make", "camera_model", "status", "duplicate_group")
        where = " OR ".join(f"COALESCE({field}, '') LIKE ? ESCAPE '\\'" for field in fields)
        rows = self.connection.execute(
            f"SELECT * FROM photos WHERE {where} ORDER BY path COLLATE NOCASE",
            (pattern,) * len(fields),
        ).fetchall()
        return [PhotoRecord(**dict(row)) for row in rows]

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "PhotoRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _is_beneath(path: str, root: str) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False
