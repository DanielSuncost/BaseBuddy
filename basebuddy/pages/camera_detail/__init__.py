"""
Camera Detail page module.

Single camera view with inference, ROIs, and controls.
"""
from flask import Blueprint

# Create blueprint
camera_detail_bp = Blueprint('camera_detail', __name__)

# Import routes and API to register them with the blueprint
from basebuddy.pages.camera_detail import routes
from basebuddy.pages.camera_detail import api
