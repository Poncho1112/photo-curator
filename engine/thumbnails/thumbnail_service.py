"""Thumbnail generation using Pillow."""

from __future__ import annotations

from pathlib import Path

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
