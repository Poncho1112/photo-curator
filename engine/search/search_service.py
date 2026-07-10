"""Catalog search service."""

from __future__ import annotations

from engine.database.models import PhotoRecord
from engine.database.repository import PhotoRepository


class SearchService:
    def __init__(self, repository: PhotoRepository) -> None:
        self.repository = repository

    def search(self, query: str) -> list[PhotoRecord]:
        return self.repository.list_all() if not query.strip() else self.repository.search_paths(query.strip())

