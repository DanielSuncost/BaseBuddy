"""First-run setup wizard."""
from flask import Blueprint

setup_bp = Blueprint("setup", __name__)

from basebuddy.pages.setup import routes, api  # noqa: E402, F401
