"""Deterministic and filesystem-safe photo naming."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_component(value: str, fallback: str = "untitled") -> str:
    """Make a value safe as one portable filename component."""
    cleaned = _UNSAFE.sub("-", value)
    cleaned = _WHITESPACE.sub("-", cleaned).strip(" .-_")
    cleaned = re.sub(r"-+", "-", cleaned)
    if not cleaned:
        cleaned = fallback
    if cleaned.upper() in _RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


def generate_name(
    original: str | Path,
    folder: str,
    captured: date | datetime,
    sha256: str,
) -> str:
    """Build ``YYYY-MM-DD_folder_original-name_hash8.ext``."""
    original = Path(original)
    stem = sanitize_component(original.stem)
    safe_folder = sanitize_component(folder, "root")
    extension = re.sub(r"[^A-Za-z0-9]", "", original.suffix).lower()
    suffix = f".{extension}" if extension else ""
    if not re.fullmatch(r"[0-9a-fA-F]{8,64}", sha256):
        raise ValueError("sha256 must contain at least eight hexadecimal characters")
    return f"{captured:%Y-%m-%d}_{safe_folder}_{stem}_{sha256[:8].lower()}{suffix}"

