"""Integrations settings page."""
from flask import Blueprint

integrations_bp = Blueprint("integrations", __name__)

from basebuddy.pages.integrations import routes  # noqa: E402, F401
