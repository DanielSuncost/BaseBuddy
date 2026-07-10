"""
Static file serving endpoints.

Serves recordings, stills, timelapse output, and detection media at repo root.
Single owner of these URLs: stills/timelapse fall back to the archive drive,
recordings are served with explicit video MIME types.
"""
from __future__ import annotations

import logging
import mimetypes
import os

from flask import Blueprint, abort, send_from_directory

from basebuddy.core.paths import abs_data_path, get_app_root, get_repo_root

logger = logging.getLogger(__name__)

static_files_api = Blueprint("static_files_api", __name__)


def _archive_root(subdir: str) -> str:
    from basebuddy.modules.config import ARCHIVE_DRIVE_PATH, ARCHIVE_FOLDER

    return os.path.join(ARCHIVE_DRIVE_PATH, ARCHIVE_FOLDER, subdir)


def _serve_with_archive_fallback(local_root: str, archive_root: str, filepath: str):
    """Serve from local root, falling back to the archive drive."""
    if os.path.isfile(os.path.join(local_root, filepath)):
        return send_from_directory(local_root, filepath)
    if os.path.isdir(archive_root) and os.path.isfile(os.path.join(archive_root, filepath)):
        return send_from_directory(archive_root, filepath)
    abort(404)


def _media_search_dirs() -> list[str]:
    from basebuddy.modules.config import BACKUP_DRIVE_PATH, MEDIA_BASE_DIR

    repo = get_repo_root()
    dirs: list[str] = []
    if MEDIA_BASE_DIR:
        dirs.append(abs_data_path(MEDIA_BASE_DIR))
    if BACKUP_DRIVE_PATH:
        dirs.append(os.path.join(BACKUP_DRIVE_PATH, "basebuddy_media"))
    dirs.append(os.path.join(repo, "media"))

    seen: set[str] = set()
    unique: list[str] = []
    for path in dirs:
        norm = os.path.normpath(path)
        if norm not in seen:
            seen.add(norm)
            unique.append(norm)
    return unique


@static_files_api.route("/recordings/<path:filepath>")
def serve_recording(filepath):
    """Serve recording video files with correct MIME type for browser playback."""
    from basebuddy.modules.config import RECORD_ROOT

    directory = abs_data_path(RECORD_ROOT)
    mimetype, _ = mimetypes.guess_type(filepath)
    if not mimetype:
        lower = filepath.lower()
        if lower.endswith(".mp4"):
            mimetype = "video/mp4"
        elif lower.endswith(".webm"):
            mimetype = "video/webm"
        elif lower.endswith(".avi"):
            mimetype = "video/x-msvideo"
    return send_from_directory(directory, filepath, mimetype=mimetype)


@static_files_api.route("/stills/<path:filepath>")
def serve_still(filepath):
    """Serve timelapse stills (local first, then archive drive)."""
    return _serve_with_archive_fallback(
        os.path.join(get_repo_root(), "stills"), _archive_root("stills"), filepath
    )


@static_files_api.route("/timelapse_output/<path:filename>")
def serve_timelapse(filename):
    """Serve timelapse videos/GIFs (local first, then archive drive)."""
    return _serve_with_archive_fallback(
        os.path.join(get_repo_root(), "timelapse_output"),
        _archive_root("timelapse_output"),
        filename,
    )


@static_files_api.route("/media/<path:subpath>")
def serve_media(subpath):
    """Serve detection thumbnails, crops, faces, etc."""
    for media_dir in _media_search_dirs():
        full_path = os.path.join(media_dir, subpath)
        if os.path.isfile(full_path):
            return send_from_directory(media_dir, subpath)

    logger.debug("Media not found %r in %s", subpath, _media_search_dirs())
    abort(404)


@static_files_api.route("/detections/<path:subpath>")
def serve_detection_media(subpath):
    """Serve event snapshot/clip files referenced as /detections/... URLs.

    Detection media lives under <media dir>/detections/; event session URLs
    are generated with a bare /detections/ prefix.
    """
    return serve_media(os.path.join("detections", subpath))
