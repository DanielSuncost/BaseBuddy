"""Pytest configuration: make the basebuddy package importable and keep tests hermetic."""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_ROOT = os.path.join(_REPO_ROOT, "basebuddy")

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Keep imports side-effect free: no camera threads, no detection workers.
os.environ.setdefault("DETECTION_ENABLED", "false")
os.environ.setdefault("HOME_SCENES_ENABLE", "false")
os.environ.setdefault("BASEBUDDY_REPO_ROOT", _REPO_ROOT)
os.environ.setdefault("BASEBUDDY_APP_ROOT", _APP_ROOT)
