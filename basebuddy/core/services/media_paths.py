"""Resolve web media paths to on-disk files."""
from __future__ import annotations

import os
from typing import Optional, Tuple

from basebuddy.core.paths import abs_data_path, get_repo_root


def url_to_filesystem(url_path: Optional[str]) -> Optional[str]:
    if not url_path:
        return None
    p = url_path.replace("\\", "/").strip()
    if p.startswith("http://") or p.startswith("https://"):
        return None
    if os.path.isabs(p) and os.path.isfile(p):
        return p

    from basebuddy.modules.config import MEDIA_BASE_DIR, MEDIA_URL_PREFIX, RECORD_ROOT

    if p.startswith(MEDIA_URL_PREFIX + "/") or p.startswith(MEDIA_URL_PREFIX):
        rel = p[len(MEDIA_URL_PREFIX):].lstrip("/")
        candidate = os.path.join(abs_data_path(MEDIA_BASE_DIR), rel)
        if os.path.isfile(candidate):
            return candidate

    for prefix, base in (
        ("/recordings/", abs_data_path(RECORD_ROOT)),
        ("/media/", abs_data_path(MEDIA_BASE_DIR)),
        ("/stills/", os.path.join(get_repo_root(), "stills")),
        ("/timelapse_output/", os.path.join(get_repo_root(), "timelapse_output")),
    ):
        if p.startswith(prefix):
            candidate = os.path.join(base, p[len(prefix):])
            if os.path.isfile(candidate):
                return candidate

    if not p.startswith("/"):
        for base in (abs_data_path(MEDIA_BASE_DIR), abs_data_path(RECORD_ROOT)):
            candidate = os.path.join(base, p)
            if os.path.isfile(candidate):
                return candidate
    return None


def best_snapshot_path(
    thumbnail_path: Optional[str], full_image_path: Optional[str] = None
) -> Tuple[Optional[str], Optional[str]]:
    for url in (full_image_path, thumbnail_path):
        fs = url_to_filesystem(url)
        if fs:
            web = url if url and (url.startswith("/") or url.startswith("http")) else None
            return fs, web or url
    return None, full_image_path or thumbnail_path
