"""
Config page module.

System configuration including thresholds, tracking, and disabled classes.
"""
from flask import Blueprint

# Create blueprint
config_bp = Blueprint('config', __name__)

# Import routes and API to register them with the blueprint
from basebuddy.pages.config import routes
from basebuddy.pages.config import api
