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

