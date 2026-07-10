"""
Camera Detail API endpoints.

Camera-specific APIs (ROIs, tracks, profiles) currently live in
basebuddy/routes/cameras.py and core/api. This module is for any
page-specific endpoints.
"""
from basebuddy.pages.camera_detail import camera_detail_bp  # noqa: F401
