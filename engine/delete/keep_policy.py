"""Pure survivor selection policies for exact-duplicate groups."""

from __future__ import annotations

from collections.abc import Iterable
from os import fspath
from typing import TypeVar


Record = TypeVar("Record")


def _shortest_path(records: list[Record]) -> Record:
    return min(records, key=lambda record: (len(fspath(record.path)), fspath(record.path)))


def choose_survivor(
    records: Iterable[Record],
    policy: str = "shortest_path",
    preferred_root: str | None = None,
) -> Record:
    """Return exactly one record to retain from a non-empty duplicate group."""
    candidates = list(records)
    if not candidates:
        raise ValueError("Cannot choose a survivor from an empty duplicate group")

    if preferred_root is not None:
        preferred = [record for record in candidates if fspath(record.path).startswith(fspath(preferred_root))]
        if preferred:
            candidates = preferred

    if policy == "shortest_path":
        return _shortest_path(candidates)
    if policy == "oldest_capture":
        dated = [record for record in candidates if record.captured_at is not None]
        if not dated:
            return _shortest_path(candidates)
        return min(
            dated,
            key=lambda record: (record.captured_at, len(fspath(record.path)), fspath(record.path)),
        )
    raise ValueError(f"Unknown keep policy: {policy}")
