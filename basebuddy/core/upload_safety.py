"""Helpers for validating user uploads (path traversal, extensions)."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Set

_SAFE_NAME = re.compile(r"^[a-zA-Z0-9._-]+$")
_IMAGE_EXTENSIONS: Set[str] = {".jpg", ".jpeg", ".png", ".webp"}


def safe_basename(name: str) -> Optional[str]:
    """Return basename if safe; reject path separators and parent refs."""
    if not name or not name.strip():
        return None
    base = os.path.basename(name.strip())
    if not base or base in (".", ".."):
        return None
    if ".." in base or "/" in base or "\\" in base:
        return None
    if not _SAFE_NAME.match(base):
        return None
    return base


def allowed_image_extension(filename: str, allowed: Optional[Set[str]] = None) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in (allowed or _IMAGE_EXTENSIONS)


def resolve_under_dir(base_dir: str | Path, *parts: str) -> Optional[Path]:
    """Resolve path and ensure it stays under base_dir."""
    base = Path(base_dir).resolve()
    try:
        target = (base.joinpath(*parts)).resolve()
    except (OSError, ValueError):
        return None
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target
