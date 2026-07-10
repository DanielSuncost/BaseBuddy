"""
Camera Wall page module.

Main dashboard showing all cameras in a grid.
"""
from flask import Blueprint

# Create blueprint
camera_wall_bp = Blueprint('camera_wall', __name__)

# Import routes and API to register them with the blueprint
from basebuddy.pages.camera_wall import routes
from basebuddy.pages.camera_wall import api
