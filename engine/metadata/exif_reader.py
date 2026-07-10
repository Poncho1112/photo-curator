"""Read a small, stable subset of image metadata."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

_DATE_FORMAT = "%Y:%m:%d %H:%M:%S"


def read_exif(path: str | Path) -> dict[str, Any]:
    """Return normalized EXIF data, or an empty mapping for unreadable images."""
    from PIL import ExifTags, Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            raw = image.getexif()
            return {ExifTags.TAGS.get(tag, str(tag)): value for tag, value in raw.items()}
    except (FileNotFoundError, OSError, UnidentifiedImageError):
        return {}


def capture_datetime(path: str | Path) -> datetime | None:
    """Return the best EXIF capture timestamp when present and valid."""
    exif = read_exif(path)
    for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
        value = exif.get(key)
        if isinstance(value, str):
            try:
                return datetime.strptime(value, _DATE_FORMAT)
            except ValueError:
                continue
    return None
