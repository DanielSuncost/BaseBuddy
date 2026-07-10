"""Plants monitoring UI."""
from flask import Blueprint

plants_bp = Blueprint("plants", __name__)

from basebuddy.pages.plants import routes  # noqa: E402, F401
