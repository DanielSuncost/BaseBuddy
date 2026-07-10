"""
Canonical paths for BaseBuddy media (recordings, stills, detection media, etc.).

Shared by main.py startup and runtime storage-policy updates so archive/usage
logic stays consistent.
"""
import os
from typing import Dict

from basebuddy.core.paths import get_app_root, get_repo_root


def project_root_from_here() -> str:
    """Repo root (runtime data directory)."""
    return get_repo_root()


def abs_under_project(project_root: str, path: str) -> str:
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(project_root, path))


def build_retention_source_dirs(project_root: str, record_root: str, media_base_dir: str) -> Dict[str, str]:
    """
    Per-service paths for retention / cloud offload.

    ``detections`` maps to MEDIA_BASE_DIR (thumbnails + detection crops).
    """
    app_root = get_app_root()
    rec = abs_under_project(project_root, record_root)
    return {
        "recordings": rec,
        "detections": media_base_dir,
        "stills": os.path.join(project_root, "stills"),
        "timelapse": os.path.join(project_root, "timelapse_output"),
        "video_thumbs": os.path.join(app_root, "static", "video_thumbs"),
        "recording_thumbs": os.path.join(app_root, "static", "recording_thumbnails"),
    }


def build_archive_source_dirs(project_root: str, record_root: str, media_base_dir: str) -> Dict[str, str]:
    """
    Labels match ArchiveService layout under <archive_folder>/<label>/...
    """
    retention = build_retention_source_dirs(project_root, record_root, media_base_dir)
    return {
        "recordings": retention["recordings"],
        "stills": retention["stills"],
        "media": retention["detections"],
        "timelapse_output": retention["timelapse"],
        "video_thumbs": retention["video_thumbs"],
    }
