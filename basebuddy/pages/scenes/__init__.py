"""Scenes page — pantry, fridge, and shelf monitoring."""
from flask import Blueprint

scenes_bp = Blueprint("scenes", __name__)

from basebuddy.pages.scenes import routes  # noqa: E402, F401
