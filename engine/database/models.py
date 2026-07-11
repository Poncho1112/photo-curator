"""Database-facing domain models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PhotoRecord:
    path: str
    sha256: str
    size: int
    captured_at: str | None = None
    width: int | None = None
    height: int | None = None
    id: int | None = None
    modified_at: float | None = None
    proposed_name: str | None = None
    date_source: str | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    status: str = "indexed"
    duplicate_group: str | None = None
    renamed_from: str | None = None
