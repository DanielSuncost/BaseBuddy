from flask import Blueprint

traffic_page_bp = Blueprint("traffic_page", __name__)

from basebuddy.pages.traffic import routes  # noqa: E402, F401
