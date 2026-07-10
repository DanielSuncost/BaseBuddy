"""
Timelapse page module.

Provides the timelapse gallery page and related API endpoints.
"""
from flask import Blueprint

# Create blueprint
timelapse_bp = Blueprint('timelapse', __name__)

# Import routes and API to register them with the blueprint
from basebuddy.pages.timelapse import routes
from basebuddy.pages.timelapse import api
