from flask import Blueprint

training_bp = Blueprint("training", __name__)

from basebuddy.pages.training import routes  # noqa: E402, F401
