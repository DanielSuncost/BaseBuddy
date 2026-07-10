"""Events review page blueprint."""
from flask import Blueprint

events_bp = Blueprint("events", __name__)

from basebuddy.pages.events import routes  # noqa: E402, F401
