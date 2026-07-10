"""
Canonical project and application directory resolution.

Repo layout::

    <repo>/                 BASEBUDDY_REPO_ROOT — config, data, logs, models
      basebuddy/            BASEBUDDY_APP_ROOT — Python packages, templates, static
        app/
        core/
        ...

Runtime data (recordings, databases) lives at repo root so Docker volumes and
local installs share the same paths without writing inside the application tree.
"""
from __future__ import annotations

import os


def get_repo_root() -> str:
    """Directory containing env.example, docker-compose.yml, and runtime data."""
    explicit = os.environ.get("BASEBUDDY_REPO_ROOT", "").strip()
    if explicit:
        return os.path.normpath(explicit)

    here = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.dirname(here)
    parent = os.path.dirname(app_dir)
    if os.path.isfile(os.path.join(parent, "env.example")):
        return parent
    if os.path.isfile(os.path.join(app_dir, "env.example")):
        return app_dir
    return app_dir


def get_app_root() -> str:
    """Directory containing app/, templates/, static/, and main.py."""
    explicit = os.environ.get("BASEBUDDY_APP_ROOT", "").strip()
    if explicit:
        return os.path.normpath(explicit)

    nested = os.path.join(get_repo_root(), "basebuddy")
    if os.path.isdir(os.path.join(nested, "app")):
        return nested
    return get_repo_root()


def ensure_import_path() -> None:
    """Make the `basebuddy` package importable when running as a script."""
    import sys

    repo_root = get_repo_root()
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


def abs_data_path(path: str) -> str:
    """Resolve a config path relative to the repo root."""
    if not path:
        return path
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(get_repo_root(), path))


def get_stills_root() -> str:
    """Timelapse source stills directory (repo root / stills)."""
    return os.path.join(get_repo_root(), "stills")


def get_timelapse_output_root() -> str:
    """Exported timelapse videos/GIFs (repo root / timelapse_output)."""
    return os.path.join(get_repo_root(), "timelapse_output")


def stills_search_roots() -> list[str]:
    """Directories that may contain camera_* still folders (local, legacy, archive)."""
    roots: list[str] = []
    for candidate in (
        get_stills_root(),
        os.path.join(get_app_root(), "stills"),
    ):
        norm = os.path.normpath(candidate)
        if norm not in roots and os.path.isdir(norm):
            roots.append(norm)
    try:
        from basebuddy.modules.config import ARCHIVE_DRIVE_PATH, ARCHIVE_FOLDER, ARCHIVE_ENABLED

        if ARCHIVE_ENABLED:
            archive = os.path.join(ARCHIVE_DRIVE_PATH, ARCHIVE_FOLDER, "stills")
            norm = os.path.normpath(archive)
            if norm not in roots and os.path.isdir(norm):
                roots.append(norm)
    except Exception:
        pass
    if not roots:
        roots.append(get_stills_root())
    return roots
