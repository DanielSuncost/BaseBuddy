"""System metrics page."""
from flask import Blueprint

metrics_bp = Blueprint("metrics", __name__)

from basebuddy.pages.metrics import routes  # noqa: E402, F401
