"""Recursive, side-effect-free discovery of supported photo files."""

from __future__ import annotations

from pathlib import Path


SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp"})


def scan_photos(root: str | Path, *, recursive: bool = True) -> list[Path]:
    root = Path(root)
    if not root.is_dir():
        return []
    candidates = root.rglob("*") if recursive else root.glob("*")
    return sorted(
        (path for path in candidates if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS),
        key=lambda path: str(path).casefold(),
    )

