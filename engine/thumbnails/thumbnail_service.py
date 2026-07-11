"""Thumbnail generation using Pillow."""

from __future__ import annotations

import stat
from pathlib import Path


DEFAULT_CACHE_LIMIT_BYTES = 500 * 1024 * 1024


def enforce_cache_limit(directory: str | Path, max_bytes: int = DEFAULT_CACHE_LIMIT_BYTES) -> None:
    """Remove oldest thumbnails until the direct-child cache fits ``max_bytes``."""
    directory = Path(directory)
    if directory.name != "thumbnails":
        raise ValueError("thumbnail cache eviction requires a directory named 'thumbnails'")
    if max_bytes < 0:
        raise ValueError("thumbnail cache limit cannot be negative")
    try:
        directory_info = directory.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(directory_info.st_mode):
        raise ValueError("thumbnail cache eviction requires a real directory")

    files: list[tuple[int, int, Path]] = []
    total_bytes = 0
    try:
        children = directory.iterdir()
        for child in children:
            try:
                info = child.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            total_bytes += info.st_size
            files.append((info.st_mtime_ns, info.st_size, child))
    except FileNotFoundError:
        return

    if total_bytes <= max_bytes:
        return
    for _mtime, size, path in sorted(files):
        try:
            path.unlink()
        except FileNotFoundError:
            total_bytes -= size
        else:
            total_bytes -= size
        if total_bytes <= max_bytes:
            break


def create_thumbnail(source: str | Path, target: str | Path, size: tuple[int, int] = (256, 256)) -> Path:
    from PIL import Image, ImageOps

    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail(size)
        if target.suffix.lower() in {".jpg", ".jpeg"} and image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        image.save(target)
    return target
